"""Side-effect-free orchestration for the first v2 editorial MVP."""

from __future__ import annotations

from typing import Any

from src.agents.collector_agent import CollectorAgent
from src.agents.editorial_agent import EditorialAgent
from src.agents.news_analyst_agent import NewsAnalystAgent
from src.domain.models import CollectionDiagnostics, WorkflowResult
from src.domain.states import WorkflowState, transition_to


class DailyEditionWorkflow:
    """Run collection, analysis, and selection without publishing artifacts."""

    def __init__(
        self,
        *,
        collector: Any | None = None,
        analyst: Any | None = None,
        editorial: Any | None = None,
    ) -> None:
        self._collector = collector or CollectorAgent()
        self._analyst = analyst or NewsAnalystAgent()
        self._editorial = editorial or EditorialAgent()

    def run(self, *, top_n: int = 10, rss_timeout: int = 30) -> WorkflowResult:
        """Return a v2 shadow result and never render, save, or publish output."""
        state = WorkflowState.CREATED
        history = [state]
        candidates = ()
        analyses = ()
        editorial_plan = None
        collection_diagnostics = CollectionDiagnostics()

        try:
            candidates, collection_diagnostics = self._collector.collect_with_diagnostics(
                top_n=top_n,
                rss_timeout=rss_timeout,
            )
            state = transition_to(state, WorkflowState.COLLECTED)
            history.append(state)

            analyses = self._analyst.analyze(candidates)
            state = transition_to(state, WorkflowState.ANALYZED)
            history.append(state)

            editorial_plan = self._editorial.select(candidates, analyses, target_count=top_n)
            state = transition_to(state, WorkflowState.SELECTED)
            history.append(state)

            state = transition_to(state, WorkflowState.COMPLETED)
            history.append(state)
            return WorkflowResult(
                state=state,
                state_history=tuple(history),
                candidates=candidates,
                analyses=analyses,
                editorial_plan=editorial_plan,
                collection_diagnostics=collection_diagnostics,
            )
        except Exception as exc:
            if state is not WorkflowState.FAILED:
                state = transition_to(state, WorkflowState.FAILED)
                history.append(state)
            return WorkflowResult(
                state=state,
                state_history=tuple(history),
                candidates=candidates,
                analyses=analyses,
                editorial_plan=editorial_plan,
                collection_diagnostics=collection_diagnostics,
                error=str(exc),
            )

    def run_existing(
        self,
        items: list[dict[str, Any]],
        *,
        top_n: int = 10,
        collection_diagnostics: CollectionDiagnostics | None = None,
    ) -> WorkflowResult:
        """Analyze production candidates without collecting or annotating them again."""
        state = WorkflowState.CREATED
        history = [state]
        candidates = ()
        analyses = ()
        editorial_plan = None
        diagnostics = collection_diagnostics or CollectionDiagnostics()

        try:
            candidates = self._collector.adapt_existing(items)
            if collection_diagnostics is None:
                diagnostics = CollectionDiagnostics(returned_candidate_count=len(candidates))
            state = transition_to(state, WorkflowState.COLLECTED)
            history.append(state)

            analyses = self._analyst.analyze(candidates)
            state = transition_to(state, WorkflowState.ANALYZED)
            history.append(state)

            editorial_plan = self._editorial.select(candidates, analyses, target_count=top_n)
            state = transition_to(state, WorkflowState.SELECTED)
            history.append(state)

            state = transition_to(state, WorkflowState.COMPLETED)
            history.append(state)
            return WorkflowResult(
                state=state,
                state_history=tuple(history),
                candidates=candidates,
                analyses=analyses,
                editorial_plan=editorial_plan,
                collection_diagnostics=diagnostics,
            )
        except Exception as exc:
            if state is not WorkflowState.FAILED:
                state = transition_to(state, WorkflowState.FAILED)
                history.append(state)
            return WorkflowResult(
                state=state,
                state_history=tuple(history),
                candidates=candidates,
                analyses=analyses,
                editorial_plan=editorial_plan,
                collection_diagnostics=diagnostics,
                error=str(exc),
            )
