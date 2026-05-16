"""Nova Imagination Engine.

Two modes:
1. World Model Simulation — "If I do X, then Y happens, then Z follows"
   Forward simulation using learned causal rules.
2. Memory Recombination — "I've seen A and B separately, what if A+B?"
   Creative scenario generation by remixing past experiences.

Used for: planning, risk assessment, creativity, anticipation.
"""

import random
import time
from .memory import SemanticMemory, EpisodicMemory, MemoryItem


class WorldModel:
    """Internal model of how the world works.
    Learned from experience, used for forward simulation."""

    def __init__(self, semantic: SemanticMemory):
        self.semantic = semantic
        self.state: dict[str, any] = {}
        self.causal_rules: list[dict] = []

    def set_state(self, state: dict):
        self.state = dict(state)

    def load_rules(self):
        self.causal_rules = []
        for rule in self.semantic.rules:
            if rule["confidence"] > 0.3:
                self.causal_rules.append(rule)
        for rel in self.semantic.relations:
            if rel["predicate"] in ("causes", "leads_to", "enables",
                                     "prevents", "increases", "decreases"):
                self.causal_rules.append({
                    "if": rel["subject"],
                    "then": f"{rel['predicate']}({rel['object']})",
                    "confidence": rel["confidence"],
                })

    def step(self, action=None):
        """Simulate one step forward."""
        events = []
        if action:
            action_str = str(action).lower()
            for rule in self.causal_rules:
                cond = str(rule["if"]).lower()
                if cond in action_str or action_str in cond:
                    if random.random() < rule["confidence"]:
                        events.append({
                            "effect": rule["then"],
                            "cause": action,
                            "confidence": rule["confidence"],
                        })
        return events

    def simulate(self, scenario, steps=5):
        """Run a multi-step simulation of a scenario."""
        self.load_rules()
        trajectory = []
        current_events = [{"effect": str(scenario), "cause": "initial",
                          "confidence": 1.0}]

        for i in range(steps):
            trajectory.append({
                "step": i,
                "events": list(current_events),
                "timestamp": time.time(),
            })
            next_events = []
            for event in current_events:
                consequences = self.step(event["effect"])
                next_events.extend(consequences)
            if not next_events:
                break
            current_events = next_events

        return SimulationResult(
            scenario=scenario,
            trajectory=trajectory,
            steps_completed=len(trajectory),
        )


class MemoryRecombiner:
    """Generates novel scenarios by recombining past experiences.
    Like dreaming — takes elements from different memories and mixes them."""

    def __init__(self, episodic: EpisodicMemory, semantic: SemanticMemory):
        self.episodic = episodic
        self.semantic = semantic

    def recombine(self, seed=None, n_scenarios=3):
        """Generate novel scenarios by mixing past experiences."""
        episodes = (self.episodic.recall(str(seed), limit=10)
                   if seed else self.episodic.recent(10))

        if len(episodes) < 2:
            return []

        scenarios = []
        for _ in range(n_scenarios):
            ep_a = random.choice(episodes)
            ep_b = random.choice(episodes)
            if ep_a.id == ep_b.id:
                continue
            scenario = {
                "type": "recombination",
                "elements": [
                    {"from": ep_a.id, "content": str(ep_a.content)[:100]},
                    {"from": ep_b.id, "content": str(ep_b.content)[:100]},
                ],
                "combined_tags": list(set(ep_a.tags + ep_b.tags)),
                "novelty": 1.0 - self._similarity(ep_a, ep_b),
                "timestamp": time.time(),
            }
            scenarios.append(scenario)

        scenarios.sort(key=lambda s: -s["novelty"])
        return scenarios

    def what_if(self, condition, base_experience=None):
        """'What if X were different?' — counterfactual imagination."""
        if base_experience:
            episodes = [base_experience]
        else:
            episodes = self.episodic.recall(str(condition), limit=5)

        counterfactuals = []
        for ep in episodes:
            cf = {
                "type": "counterfactual",
                "original": str(ep.content)[:100],
                "condition": condition,
                "modified": f"If {condition}, then the outcome of '{str(ep.content)[:50]}' might differ",
                "original_tags": ep.tags,
                "timestamp": time.time(),
            }
            counterfactuals.append(cf)
        return counterfactuals

    @staticmethod
    def _similarity(a: MemoryItem, b: MemoryItem):
        a_words = set(str(a.content).lower().split())
        b_words = set(str(b.content).lower().split())
        if not a_words or not b_words:
            return 0.0
        return len(a_words & b_words) / len(a_words | b_words)


class SimulationResult:
    """Result of an imagination simulation."""

    def __init__(self, scenario, trajectory, steps_completed):
        self.scenario = scenario
        self.trajectory = trajectory
        self.steps_completed = steps_completed

    def outcomes(self):
        if not self.trajectory:
            return []
        final = self.trajectory[-1]
        return [e["effect"] for e in final.get("events", [])]

    def risks(self, threshold=0.3):
        all_events = []
        for step in self.trajectory:
            for event in step.get("events", []):
                effect = str(event.get("effect", "")).lower()
                if any(w in effect for w in ("fail", "lose", "damage", "harm",
                                              "break", "decrease", "risk",
                                              "danger", "negative")):
                    all_events.append({
                        "risk": event["effect"],
                        "step": step["step"],
                        "confidence": event.get("confidence", 0.5),
                    })
        return [e for e in all_events if e["confidence"] >= threshold]

    def opportunities(self, threshold=0.3):
        all_events = []
        for step in self.trajectory:
            for event in step.get("events", []):
                effect = str(event.get("effect", "")).lower()
                if any(w in effect for w in ("succeed", "gain", "improve",
                                              "increase", "benefit",
                                              "opportunity", "positive")):
                    all_events.append({
                        "opportunity": event["effect"],
                        "step": step["step"],
                        "confidence": event.get("confidence", 0.5),
                    })
        return [e for e in all_events if e["confidence"] >= threshold]

    def __repr__(self):
        return (f"SimulationResult(scenario={self.scenario!r}, "
                f"steps={self.steps_completed}, "
                f"outcomes={len(self.outcomes())})")


class ImaginationEngine:
    """Combined imagination: world model simulation + memory recombination."""

    def __init__(self, semantic: SemanticMemory, episodic: EpisodicMemory):
        self.world_model = WorldModel(semantic)
        self.recombiner = MemoryRecombiner(episodic, semantic)
        self.semantic = semantic
        self.episodic = episodic

    def simulate(self, scenario, steps=5):
        return self.world_model.simulate(scenario, steps)

    def imagine_scenarios(self, seed=None, n=3):
        return self.recombiner.recombine(seed, n)

    def what_if(self, condition, base=None):
        return self.recombiner.what_if(condition, base)

    def anticipate(self, current_situation):
        """Proactive imagination: what might happen next?"""
        sim = self.simulate(current_situation, steps=3)
        risks = sim.risks()
        opportunities = sim.opportunities()
        creative = self.imagine_scenarios(seed=current_situation, n=2)

        return {
            "predicted_outcomes": sim.outcomes(),
            "risks": risks,
            "opportunities": opportunities,
            "creative_scenarios": creative,
            "simulation": sim,
        }

    def __repr__(self):
        return "ImaginationEngine(world_model + recombiner)"
