from typing import Collection

import structlog

from autopr.actions.base import ContextDict
from autopr.actions.utils.commit import PullRequestDescription, CommitPlan
from autopr.agents.base import Agent
from autopr.models.events import EventUnion, IssueLabelEvent

log = structlog.get_logger()


class PlanAndCode(Agent):
    """
    A simple agent that:
    - plans commits from issues or pull request comments,
    - opens and responds to pull requests,
    - writes commits to the pull request.
    """

    #: The ID of the agent, used to identify it in the settings
    id = "plan_and_code"

    def __init__(
        self,
        *args,
        planning_actions: Collection[str] = (
            "plan_pull_request",
            "request_more_information"
        ),
        codegen_actions: Collection[str] = (
            'new_file',
            'edit_file',
        ),
        max_codegen_iterations: int = 5,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.planning_actions = planning_actions
        self.codegen_actions = codegen_actions
        self.max_codegen_iterations = max_codegen_iterations

    def write_commit(
        self,
        commit_plan: CommitPlan,
        context: ContextDict,
        context_headings: dict[str, str]
    ) -> ContextDict:
        self.publish_service.start_section(f"🔨 Writing commit {commit_plan.commit_message}")

        # Set the current commit in the context
        context['current_commit'] = commit_plan

        # Clear action_history in the context for each commit
        context['action_history'] = []

        # Generate the changes
        context = self.action_service.run_actions_iteratively(
            self.codegen_actions,
            context,
            context_headings={
                'current_commit': 'Commit we are currently generating',
                'action_history': 'Actions that have been run so far',
                **context_headings,
            },
            max_iterations=self.max_codegen_iterations,
            include_finished=True,
        )

        # Show the diff in the progress report
        diff = self.diff_service.get_diff()
        if diff:
            self.publish_service.publish_code_block(
                heading="Diff",
                code=diff,
                language="diff",
            )
            self.publish_service.end_section(f"✅ Committed {commit_plan.commit_message}")
        else:
            self.publish_service.end_section(f"⚠️ Empty commit {commit_plan.commit_message}")

        # Commit and push the changes
        self.commit_service.commit(commit_plan.commit_message, push=True)

        return context

    def create_pull_request(
        self,
        event: IssueLabelEvent,
    ) -> None:
        # Create new branch
        self.commit_service.overwrite_new_branch()

        issue = event.issue

        # Initialize the context
        context = ContextDict(
            issue=issue,
        )

        # Generate the pull request plan (commit messages and relevant filepaths)
        context = self.action_service.run_actions_iteratively(
            self.planning_actions,
            context,
            max_iterations=1,
        )

        # Get the pull request description from the context
        if 'pull_request_description' not in context:
            # Stop the agent if the action did not return a pull request description
            return
        pr_desc = context['pull_request_description']
        if not isinstance(pr_desc, PullRequestDescription):
            raise TypeError(f"Actions returned a pull request description of type "
                            f"{type(pr_desc)} instead of PullRequestDescription")

        # Publish the description
        self.publish_service.set_pr_description(pr_desc.title, pr_desc.body)

        for current_commit in pr_desc.commits:
            context = self.write_commit(
                current_commit,
                context,
                context_headings={
                    'pull_request_description': 'Plan for the pull request',
                },
            )

    def handle_event(
        self,
        event: EventUnion,
    ) -> None:
        if isinstance(event, IssueLabelEvent):
            self.create_pull_request(event)
        else:
            raise NotImplementedError(f"Event type {type(event)} not supported")