from inline_snapshot import snapshot

from hogvm.python.operation import HOGQL_BYTECODE_VERSION
from posthog.cdp.templates.webhook.template_webhook import template as template_webhook
from posthog.management.commands.migrate_action_webhooks import (
    migrate_action_webhooks,
    migrate_all_teams_action_webhooks,
)
from posthog.models import Action, Team
from posthog.models.hog_functions.hog_function import HogFunction
from posthog.test.base import BaseTest


advanced_message_format = """Event: [event] [event.event] [event.link] [event.uuid]
Person: [person] [person.link] [person.properties.foo.bar]
Groups: [groups.organization]  [groups.organization.properties.foo.bar]
Action: [action.name] [action.link]"""


class TestMigrateActionWebhooks(BaseTest):
    action: Action

    def setUp(self):
        super().setUp()
        self.team.slack_incoming_webhook = "https://webhooks.slack.com/123"
        self.team.save()
        self.action = Action.objects.create(
            created_by=self.user,
            name="Test Action",
            team_id=self.team.id,
            slack_message_format="[event] triggered by [person]",
            post_to_slack=True,
            steps_json=[
                {
                    "event": None,  # All events
                }
            ],
        )

    def test_dry_run(self):
        migrate_action_webhooks(action_ids=[], team_ids=[], dry_run=True)
        assert not HogFunction.objects.exists()
        self.action.refresh_from_db()
        assert self.action.post_to_slack is True  # no change

    def test_inert_run(self):
        migrate_action_webhooks(action_ids=[], team_ids=[], inert=True)
        assert HogFunction.objects.exists()
        hog = HogFunction.objects.first()
        assert hog is not None
        assert "print" in hog.hog
        assert "fetch" not in hog.hog
        self.action.refresh_from_db()
        assert self.action.post_to_slack is True  # no change
        assert hog.name == f"[CDP-TEST-HIDDEN] Webhook for action {self.action.id} (Test Action)"

    def test_only_specified_team(self):
        migrate_action_webhooks(action_ids=[], team_ids=[9999])
        assert not HogFunction.objects.exists()
        migrate_action_webhooks(action_ids=[], team_ids=[self.team.id])
        assert HogFunction.objects.exists()

    def test_only_specified_actiojns(self):
        migrate_action_webhooks(action_ids=[9999], team_ids=[])
        assert not HogFunction.objects.exists()
        migrate_action_webhooks(action_ids=[self.action.id], team_ids=[])
        assert HogFunction.objects.exists()

    def test_migrates_base_action_config_correctly(self):
        migrate_action_webhooks(action_ids=[], team_ids=[], dry_run=False)
        self.action.refresh_from_db()

        hog_functons = HogFunction.objects.all()
        assert len(hog_functons) == 1
        hog_function = hog_functons[0]

        assert hog_function.name == f"Webhook for action {self.action.id} (Test Action)"
        assert hog_function.filters == {
            "actions": [{"id": f"{self.action.id}", "name": "Test Action", "type": "actions", "order": 0}],
            "bytecode": ["_H", HOGQL_BYTECODE_VERSION, 29, 3, 1, 4, 1],
        }
        assert hog_function.hog == template_webhook.hog
        assert hog_function.inputs_schema == template_webhook.inputs_schema
        assert hog_function.template_id == template_webhook.id
        assert hog_function.bytecode
        assert hog_function.enabled
        assert hog_function.icon_url == template_webhook.icon_url

        assert self.action.post_to_slack is False

    def test_migrates_message_format(self):
        migrate_action_webhooks(action_ids=[], team_ids=[], dry_run=False)
        hog_function = HogFunction.objects.all()[0]

        assert hog_function.inputs["url"]["value"] == "https://webhooks.slack.com/123"
        assert hog_function.inputs["method"]["value"] == "POST"
        assert hog_function.inputs["body"]["value"] == snapshot(
            {
                "text": "{event.event} triggered by {person.name}",
                "blocks": [
                    {
                        "text": {
                            "text": "<{event.url}|{event.event}> triggered by <{person.url}|{person.name}>",
                            "type": "mrkdwn",
                        },
                        "type": "section",
                    }
                ],
            }
        )

    def test_migrates_message_format_not_slack(self):
        self.team.slack_incoming_webhook = "https://webhooks.other.com/123"
        self.team.save()
        migrate_action_webhooks(action_ids=[], team_ids=[], dry_run=False)
        hog_function = HogFunction.objects.all()[0]

        assert hog_function.inputs["url"]["value"] == "https://webhooks.other.com/123"
        assert hog_function.inputs["body"]["value"] == snapshot(
            {"text": "[{event.event}]({event.url}) triggered by [{person.name}]({person.url})"}
        )

    def test_migrates_advanced_message_format(self):
        self.action.slack_message_format = advanced_message_format
        self.action.save()
        migrate_action_webhooks(action_ids=[], team_ids=[], dry_run=False)
        hog_function = HogFunction.objects.all()[0]

        assert (
            hog_function.inputs["body"]["value"]["text"]
            == """Event: {event.event} {event.event} {event.url} {event.uuid}
Person: {person.name} {person.url} {person.properties.foo.bar}
Groups: {groups.organization.url}  {groups.organization.properties.foo.bar}
Action: Test Action {project.url}/data-management/actions/1""".replace("1", str(self.action.id))
        )

        assert hog_function.inputs["body"]["value"]["blocks"] == [
            {
                "text": {
                    "text": """Event: <{event.url}|{event.event}> {event.event} {event.url} {event.uuid}
Person: <{person.url}|{person.name}> {person.url} {person.properties.foo.bar}
Groups: {groups.organization.url}  {groups.organization.properties.foo.bar}
Action: <{project.url}/data-management/actions/1|Test Action> {project.url}/data-management/actions/1""".replace(
                        "1", str(self.action.id)
                    ),
                    "type": "mrkdwn",
                },
                "type": "section",
            }
        ]

    def test_migrates_advanced_message_format_not_slack(self):
        self.action.slack_message_format = advanced_message_format
        self.action.save()
        self.team.slack_incoming_webhook = "https://webhooks.other.com/123"
        self.team.save()
        migrate_action_webhooks(action_ids=[], team_ids=[], dry_run=False)
        hog_function = HogFunction.objects.all()[0]

        assert hog_function.inputs["body"]["value"] == {
            "text": """\
Event: [{event.event}]({event.url}) {event.event} {event.url} {event.uuid}
Person: [{person.name}]({person.url}) {person.url} {person.properties.foo.bar}
Groups: {groups.organization.url}  {groups.organization.properties.foo.bar}
Action: [Test Action]({project.url}/data-management/actions/1) {project.url}/data-management/actions/1\
""".replace("1", str(self.action.id))
        }

    def test_migrate_large_number_of_actions_across_teams(self):
        organization = self.organization
        number_of_teams = 3
        number_of_actions_per_team = 150

        # Create 3 teams
        teams = [
            Team.objects.create(
                name=f"Team {i}", slack_incoming_webhook="https://slack.com/webhook", organization=organization
            )
            for i in range(number_of_teams)
        ]

        # Create 150 actions for each team (450 total)
        actions = []
        for team in teams:
            for i in range(number_of_actions_per_team):
                actions.append(
                    Action.objects.create(
                        team=team,
                        name=f"Action {i} for team {team.id}",
                        post_to_slack=True,
                        deleted=False,
                        steps_json=[{"event": None}],
                    )
                )

        # Run the migration
        migrate_all_teams_action_webhooks()

        # Count resulting HogFunctions
        hog_function_count = HogFunction.objects.filter(team_id__in=[team.id for team in teams]).count()
        self.assertEqual(
            hog_function_count,
            number_of_teams * number_of_actions_per_team,
            f"Expected {number_of_teams * number_of_actions_per_team} HogFunctions, but got {hog_function_count}",
        )

        # Verify all actions for test teams have been properly migrated (meaning post_to_slack is False and deleted is False)
        actions_to_migrate = Action.objects.filter(
            team_id__in=[team.id for team in teams], post_to_slack=True, deleted=False
        )
        self.assertEqual(
            actions_to_migrate.count(),
            0,
            f"Expected 0 actions left to migrate, but found {actions_to_migrate.count()}. "
            + f"Actions still needing migration: {list(actions_to_migrate.values_list('id', 'team_id'))}",
        )
