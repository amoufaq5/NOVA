"""Tests for the Nova cognitive engines (reasoning, imagination, learning)."""
import pytest
from nova.runtime.memory import EpisodicMemory, SemanticMemory, ProceduralMemory, WorkingMemory
from nova.runtime.reasoning import ReasoningEngine
from nova.runtime.imagination import ImaginationEngine
from nova.runtime.learning import LearningEngine, ReinforcementLearner, BayesianLearner, PatternExtractor
from nova.runtime.mind import Mind


class TestReasoningEngine:
    def setup_method(self):
        self.semantic = SemanticMemory()
        self.episodic = EpisodicMemory()
        self.engine = ReasoningEngine(self.semantic, self.episodic)

    def test_reason(self):
        self.semantic.store_fact("fire", "causes", "heat")
        result = self.engine.reason("fire")
        assert result is not None

    def test_predict_consequence(self):
        self.semantic.store_rule("if fire", "then heat", confidence=0.9)
        results = self.engine.predict_consequence("fire")
        assert isinstance(results, list)

    def test_check_consistency(self):
        self.semantic.store_belief("A is true", 0.9)
        result = self.engine.check_consistency("A is true")
        assert result is not None


class TestImaginationEngine:
    def setup_method(self):
        self.semantic = SemanticMemory()
        self.episodic = EpisodicMemory()
        self.engine = ImaginationEngine(self.semantic, self.episodic)

    def test_simulate(self):
        result = self.engine.simulate("walking in rain", steps=3)
        assert result is not None
        outcomes = result.outcomes()
        assert isinstance(outcomes, list)

    def test_risks_and_opportunities(self):
        result = self.engine.simulate("investing money", steps=5)
        risks = result.risks()
        opps = result.opportunities()
        assert isinstance(risks, list)
        assert isinstance(opps, list)

    def test_imagine_scenarios(self):
        self.episodic.store(content="went to park", importance=0.5, tags=["outdoor"])
        scenarios = self.engine.imagine_scenarios(seed="outdoor", n=3)
        assert isinstance(scenarios, list)

    def test_anticipate(self):
        result = self.engine.anticipate("storm approaching")
        assert result is not None
        assert "risks" in result or "predictions" in result or isinstance(result, dict)


class TestLearningEngine:
    def setup_method(self):
        self.working = WorkingMemory()
        self.episodic = EpisodicMemory()
        self.semantic = SemanticMemory()
        self.procedural = ProceduralMemory()
        self.engine = LearningEngine(
            self.working, self.episodic, self.semantic, self.procedural)

    def test_process_experience_reinforcement(self):
        result = self.engine.process_experience({
            "situation": "danger",
            "action": "run",
            "outcome": "safe",
            "reward": 0.9,
        })
        assert result["reinforcement"] is not None

    def test_process_experience_bayesian(self):
        self.semantic.store_belief("it will rain", 0.5)
        result = self.engine.process_experience({
            "observation": "dark clouds forming",
            "tags": ["weather"],
        })
        assert result["bayesian"] is not None

    def test_consolidate(self):
        for i in range(5):
            self.episodic.store(
                content=f"event{i}",
                importance=0.5,
                tags=["test"],
                context={"cause": "test", "outcome": "ok"},
            )
        result = self.engine.consolidate()
        assert "new_rules" in result

    def test_reinforcement_learner(self):
        rl = ReinforcementLearner(self.procedural)
        rl.record("danger", "run", "safe", 0.9)
        best = rl.best_action("danger")
        assert best is not None


class TestMind:
    def setup_method(self):
        self.mind = Mind(name="TestMind", config={
            "working_capacity": 5,
            "max_episodes": 1000,
        })

    def test_creation(self):
        assert self.mind.name == "TestMind"
        assert self.mind.working.capacity == 5
        assert self.mind.state == "idle"

    def test_perceive(self):
        result = self.mind.perceive("hello world")
        assert result["content"] == "hello world"
        assert len(self.mind.working) > 0

    def test_think(self):
        result = self.mind.think("what is meaning")
        assert result is not None

    def test_imagine(self):
        result = self.mind.imagine("flying car", steps=3)
        assert "simulation" in result
        assert "outcomes" in result

    def test_learn_from(self):
        result = self.mind.learn_from({
            "situation": "test",
            "action": "study",
            "outcome": "pass",
            "reward": 0.8,
        })
        assert result is not None

    def test_act(self):
        result = self.mind.act("explore")
        assert result["executed"]
        assert len(self.mind.episodic) > 0

    def test_believe_and_recall(self):
        self.mind.believe("earth is round", 0.99)
        belief = self.mind.get_belief("earth is round")
        assert belief["confidence"] == 0.99

    def test_know_and_recall(self):
        self.mind.know("Python", "is_a", "language")
        results = self.mind.recall("Python", source="semantic")
        assert results["semantic"]

    def test_goals(self):
        self.mind.add_goal("learn math", 0.8)
        assert len(self.mind.goals) == 1
        self.mind.complete_goal("learn math")
        assert len(self.mind.goals) == 0

    def test_drives(self):
        assert "curiosity" in self.mind.drives
        assert "self_preservation" in self.mind.drives

    def test_status(self):
        status = self.mind.status()
        assert status["name"] == "TestMind"
        assert "working_memory" in status
        assert "episodic_memory" in status

    def test_idle_cycle(self):
        result = self.mind.idle_cycle()
        assert result is not None
        assert result["type"] in ("idle", "goal_pursuit")

    def test_event_handlers(self):
        events_received = []
        self.mind.on("test_event", lambda data: events_received.append(data))
        self.mind._fire_event("test_event", {"msg": "hello"})
        assert len(events_received) == 1
