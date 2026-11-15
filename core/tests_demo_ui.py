"""Front-end tests for the public demo banner, message toasts and the ground-delay panel."""

from django.contrib.messages import constants as message_levels
from django.contrib.messages.storage.base import Message
from django.core.management import call_command
from django.template import Context, Template
from django.template.loader import render_to_string
from django.test import TestCase
from django.urls import reverse

from alerts.models import Alert
from core.alert_texts import ALERT_TEXTS
from core.models import AstronautProfile

BANNER_TEXT = 'Public demo, read-only. Simulated crew data. Not a medical device.'
FORCE_PUBLIC_DEMO = {'value': False}


def force_public_demo(request):
    """Stand-in for the context processor that feat/public-demo adds."""
    return {'public_demo': FORCE_PUBLIC_DEMO['value']}


TEST_PROCESSORS = [
    'django.template.context_processors.request',
    'django.contrib.auth.context_processors.auth',
    'django.contrib.messages.context_processors.messages',
    'core.context_processors.spacepulse_context',
    'core.tests_demo_ui.force_public_demo',
]


def _templates_with_test_processor():
    from django.conf import settings

    config = dict(settings.TEMPLATES[0])
    config['OPTIONS'] = dict(config['OPTIONS'], context_processors=TEST_PROCESSORS)
    return [config]


class DemoUiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('seed_demo', verbosity=0)
        cls.astro = AstronautProfile.objects.get(email='amara.reyes@mission.example')

    def setUp(self):
        FORCE_PUBLIC_DEMO['value'] = False
        self.addCleanup(FORCE_PUBLIC_DEMO.update, value=False)
        resp = self.client.post(reverse('core:login'), {
            'email': 'amara.reyes@mission.example',
            'password': 'validAccess2026',
        })
        self.assertEqual(resp.status_code, 302)

    # --- banner -------------------------------------------------------------

    def test_banner_shown_only_when_public_demo(self):
        with self.settings(TEMPLATES=_templates_with_test_processor()):
            html = self.client.get(reverse('core:dashboard')).content.decode()
            self.assertNotIn('id="public-demo-banner"', html)
            FORCE_PUBLIC_DEMO['value'] = True
            for url in (reverse('core:dashboard'), reverse('alerts:list')):
                html = self.client.get(url).content.decode()
                self.assertIn('id="public-demo-banner"', html, url)
                self.assertIn(BANNER_TEXT, html, url)
                self.assertIn('role="status"', html)

    # --- messages toast -----------------------------------------------------

    def test_messages_partial_renders_levels_accessibly(self):
        msgs = [
            Message(message_levels.INFO, 'Info text'),
            Message(message_levels.WARNING, 'Read-only warning'),
            Message(message_levels.ERROR, 'Error text'),
        ]
        html = render_to_string('partials/messages.html', {'messages': msgs})
        self.assertIn('Read-only warning', html)
        self.assertEqual(html.count('role="alert"'), 2)   # warning + error
        self.assertEqual(html.count('role="status"'), 1)  # info
        self.assertEqual(html.count('aria-label="Dismiss message"'), 3)

    def test_messages_partial_empty_renders_nothing(self):
        self.assertEqual(render_to_string('partials/messages.html', {'messages': []}).strip(), '')

    def test_base_layout_includes_messages_partial(self):
        resp = self.client.get(reverse('core:dashboard'))
        self.assertTemplateUsed(resp, 'partials/messages.html')
        self.assertNotContains(resp, 'id="message-toasts"')  # nothing queued, so no toast container

    # --- ground-delay panel -------------------------------------------------

    def _render_tag(self, tag_args, context=None):
        template = Template('{% load ground_panel %}{% ground_delay_panel ' + tag_args + ' %}')
        return template.render(Context(context or {}))

    def test_panel_absent_without_alerts(self):
        self.assertNotIn('id="ground-panel"', self._render_tag('alerts=alerts', {'alerts': []}))

    def test_panel_uses_worst_alert_with_verbatim_science_lead_text(self):
        caution = Alert(astronaut=self.astro, metric='sleep', severity='caution', title='t', message='m', mission_day=142)
        critical = Alert(astronaut=self.astro, metric='resting_hr', severity='critical', title='t', message='m', mission_day=142)
        html = self._render_tag('alerts=alerts', {'alerts': [caution, critical]})
        parts = ALERT_TEXTS[('resting_hr', 'red')]
        for key in ('what', 'why', 'action', 'escalate'):
            self.assertIn(parts[key], html)
        self.assertNotIn(ALERT_TEXTS[('sleep', 'amber')]['what'], html)
        self.assertIn('Ground is ~20 minutes away', html)
        self.assertIn('Act now on board', html)
        self.assertIn('Packet to flight surgeon', html)
        self.assertIn('6&ndash;44 min', html)  # round trip of the 3-22 min one-way range
        self.assertIn('Not sent (simulated uplink)', html)
        self.assertIn('not a diagnosis', html)

    def test_panel_skips_acknowledged_and_nominal_alerts(self):
        acked = Alert(astronaut=self.astro, metric='sleep', severity='caution', title='t', message='m', acknowledged=True)
        nominal = Alert(astronaut=self.astro, metric='hrv', severity='nominal', title='t', message='m')
        self.assertNotIn('id="ground-panel"', self._render_tag('alerts=alerts', {'alerts': [acked, nominal]}))

    def test_backend_override_wins_over_fallbacks(self):
        alert = Alert(astronaut=self.astro, metric='sleep', severity='caution', title='t', message='m')
        html = self._render_tag('alert=alert', {
            'alert': alert,
            'ground_panel': {
                'summary': ['Line from backend'],
                'sent_at': '2026-11-15T10:00:00Z',
                'packet_id': 'MED-1',
                'one_way_min': 4,
                'one_way_max': 20,
            },
        })
        self.assertIn('Line from backend', html)
        self.assertIn('data-sent-at="2026-11-15T10:00:00Z"', html)
        self.assertIn('#MED-1', html)
        self.assertIn('8&ndash;40 min', html)

    def test_pages_render_panel_for_seeded_active_alert(self):
        alert = Alert.objects.filter(astronaut=self.astro, acknowledged=False).exclude(severity='nominal').first()
        self.assertIsNotNone(alert, 'seed data should contain an active caution/critical alert')
        for url in (reverse('core:dashboard'), reverse('alerts:detail', args=[alert.id])):
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 200, url)
            self.assertContains(resp, 'id="ground-panel"')
            self.assertContains(resp, 'Packet to flight surgeon')


class BodyFigureTests(TestCase):
    def _cards(self, **statuses):
        keys = ['resting_hr', 'hrv', 'sleep', 'exercise', 'body_temp', 'mood']
        return [
            {'key': k, 'label': k.title(), 'status': statuses.get(k, 'nominal'),
             'formatted_value': '1', 'unit': 'u'}
            for k in keys
        ]

    def _render(self, cards):
        return Template('{% load body_figure %}{% body_figure cards %}').render(Context({'cards': cards}))

    def test_region_takes_worst_status_and_links_to_first_card(self):
        html = self._render(self._cards(mood='critical', sleep='nominal', hrv='caution'))
        self.assertIn('data-region="head" href="#metric-sleep"', html)
        self.assertRegex(html, r'data-status="critical" data-region="head"')
        self.assertRegex(html, r'data-status="caution" data-region="chest"')
        self.assertRegex(html, r'data-status="nominal" data-region="skin"')
        self.assertIn('href="#metric-mood"', html)  # the list links every indicator

    def test_unknown_status_is_no_data(self):
        html = self._render(self._cards(body_temp='no_data', exercise='whatever'))
        self.assertRegex(html, r'data-status="no_data" data-region="skin"')
        self.assertRegex(html, r'data-status="no_data" data-region="limbs"')

    def test_no_cards_renders_nothing(self):
        self.assertNotIn('body-map', self._render([]))

    def test_dashboard_has_body_map_and_card_anchors(self):
        call_command('seed_demo', verbosity=0)
        self.client.post(reverse('core:login'), {'email': 'amara.reyes@mission.example', 'password': 'validAccess2026'})
        html = self.client.get(reverse('core:dashboard')).content.decode()
        self.assertIn('id="body-map"', html)
        for key in ('sleep', 'mood', 'resting_hr', 'hrv', 'exercise', 'body_temp'):
            self.assertIn(f'id="metric-{key}"', html)


class GroundSupportSummaryTests(TestCase):
    """The Ground Support summary card is built from health_* keys, never fixed copy."""

    REMOVED = (
        'Mostly nominal. Sleep has dropped four days in a row.',
        'down from 7.3 h four days ago',
        'Ground Medical Officer intervention recommended',
        'REM deficit',
        'Target 78+',
        'Physiological Condition: Stable',
    )

    def _render(self, **ctx):
        from django.contrib.auth.models import AnonymousUser
        from django.test import RequestFactory

        request = RequestFactory().get('/ground-support/')
        request.user = AnonymousUser()
        context = {'active_nav': 'ground_support', 'ground_messages': [], 'selected_filter': 'all'}
        context.update(ctx)
        return render_to_string('core/ground_support.html', context, request=request)

    def test_each_status_renders_its_label_pill_and_headline(self):
        cases = {
            'nominal': ('Nominal', 'text-[#22E3A0]', 'nominal range'),
            'caution': ('Caution', 'text-[#FFB020]', 'caution range'),
            'critical': ('Critical', 'text-error', 'critical range'),
            'no_data': ('No data', 'text-outline', 'Not enough baseline data'),
        }
        for status, (label, pill_class, headline) in cases.items():
            with self.subTest(status=status):
                html = self._render(health_status=status, health_label=label, health_color='#123456',
                                    health_score=90, health_dashoffset=40, physio_reserve='86.8')
                self.assertIn(f'data-health-pill="{status}"', html)
                self.assertIn(f'{label} Status', html)
                self.assertIn(f'Physiological Condition: {label}', html)
                self.assertIn(pill_class, html)
                self.assertIn(headline, html)

    def test_backend_color_used_for_ring_when_valid_and_ignored_when_not(self):
        html = self._render(health_status='caution', health_label='Caution', health_color='#abcdef')
        self.assertIn('stroke="#abcdef"', html)
        html = self._render(health_status='caution', health_label='Caution', health_color='red;"><script>')
        self.assertNotIn('<script>', html.split('Primary Arc')[1].split('</svg>')[0])
        self.assertIn('stroke="#FFB020"', html)

    def test_page_renders_without_any_health_keys_as_no_data(self):
        html = self._render()
        self.assertIn('data-health-pill="no_data"', html)
        self.assertIn('No data Status', html)
        self.assertNotIn('Nominal Status', html)
        self.assertIn('Not enough baseline data', html)

    def test_zero_offset_means_full_ring_not_empty(self):
        html = self._render(health_status='nominal', health_label='Nominal', health_score=100, health_dashoffset=0)
        self.assertIn('stroke-dashoffset="0"', html)

    def test_unknown_status_is_no_data_not_nominal(self):
        html = self._render(health_status='great', health_label='Nominal')
        self.assertIn('data-health-pill="no_data"', html)
        self.assertNotIn('Nominal Status', html)

    def test_removed_static_copy_is_gone(self):
        for ctx in ({}, {'health_status': 'nominal', 'health_label': 'Nominal'}):
            html = self._render(sleep_latest='5.8', sleep_base='7.2', **ctx)
            for text in self.REMOVED:
                self.assertNotIn(text, html, text)

    def test_physio_reserve_keeps_experimental_tag(self):
        html = self._render(physio_reserve='86.8')
        self.assertIn('86.8%', html)
        self.assertIn('Experimental · not a validated measure', html)
        self.assertIn('No data', self._render())


class GroundSupportNoStaticCautionCardTests(TestCase):
    """The hard-coded 'Caution Notice' sleep card (with a melatonin line) must stay gone."""

    @classmethod
    def setUpTestData(cls):
        call_command('seed_demo', verbosity=0)

    def test_static_caution_card_absent_from_ground_support_page(self):
        resp = self.client.post(reverse('core:login'), {
            'email': 'amara.reyes@mission.example',
            'password': 'validAccess2026',
        })
        self.assertEqual(resp.status_code, 302)
        html = self.client.get(reverse('core:ground_support')).content.decode()
        for text in ('Melatonin', '#AL-892', 'Caution Notice', 'Sleep is trending down',
                     'Flight Surgeon Verification: Pending'):
            self.assertNotIn(text, html, text)
        self.assertIn('Recent Signals', html)
        self.assertIn('Cabin CO2 steady', html)  # the CO2 card is left as it was


class AlertDetailNoFakeValuesTests(TestCase):
    """No invented packet id, action time or crew name on the alert detail page; base.html reading errors."""

    @classmethod
    def setUpTestData(cls):
        call_command('seed_demo', verbosity=0)
        cls.astro = AstronautProfile.objects.get(email='amara.reyes@mission.example')

    def setUp(self):
        resp = self.client.post(reverse('core:login'), {
            'email': 'amara.reyes@mission.example',
            'password': 'validAccess2026',
        })
        self.assertEqual(resp.status_code, 302)
        self.alert = Alert.objects.filter(astronaut=self.astro, acknowledged=False).first()
        self.assertIsNotNone(self.alert)

    def _html(self):
        return self.client.get(reverse('alerts:detail', args=[self.alert.id])).content.decode()

    def test_fake_static_values_absent(self):
        html = self._html()
        for text in ('MED-8812', '08:12 UTC', 'Cabin lighting dimmed', 'Protocol auto-engaged',
                     'Cmdr. Reyes', 'Signal Queued'):
            self.assertNotIn(text, html, text)

    def test_action_status_uses_real_alert_data(self):
        html = self._html()
        self.assertIn('Raised %s UTC' % self.alert.timestamp.strftime('%H:%M'), html)
        self.assertIn('Alert #%d' % self.alert.id, html)
        self.assertIn('Simulated, nothing is sent', html)
        self.assertIn('No action logged', html)
        self.assertNotIn('has been acknowledged', html)

    def test_bio_sensor_card_is_labelled_simulated(self):
        html = self._html()
        start = html.index('Bio-Sensor ID')
        end = html.index('Confidence Value', start)
        card = html[start:end]
        self.assertIn('(simulated)', card)
        self.assertIn('POLARIS-BIO-04', card)

    def test_acknowledged_alert_says_so(self):
        Alert.objects.filter(pk=self.alert.pk).update(acknowledged=True)
        html = self._html()
        self.assertIn('This alert has been acknowledged.', html)
        self.assertNotIn('Nothing has been done on this alert yet', html)

    def test_reading_modal_error_region_is_accessible_and_handler_surfaces_server_error(self):
        html = self.client.get(reverse('core:dashboard')).content.decode()
        self.assertIn('id="reading-error" role="alert" hidden', html)
        self.assertIn("Couldn't save this reading.", html)
        self.assertIn('data.error', html)            # server message is used when present
        self.assertIn('await response.json()', html)  # parsed inside try/catch
        self.assertNotIn('<span>Error</span>', html)  # no more flash-and-reset
