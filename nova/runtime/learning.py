"""Nova Learning Engine.

Three learning mechanisms combined:
1. Reinforcement — actions have consequences → update behavior strength
2. Bayesian — observe evidence → update beliefs probabilistically
3. Pattern Extraction — repeated experiences → extract general rules

Continuous consolidation: important memories strengthen, irrelevant ones fade.
"""

import time
from .memory import (WorkingMemory, EpisodicMemory, SemanticMemory,
                     ProceduralMemory, MemoryItem)


class ReinforcementLearner:
    """Learn from consequences of actions.
    Good outcome → strengthen the action. Bad outcome → weaken it."""

    def __init__(self, procedural: ProceduralMemory):
        self.procedural = procedural
        self.learning_rate = 0.1
        self.history: list[dict] = []

    def record(self, situation, action, outcome, reward):
        episode = {
            "situation": situation,
            "action": action,
            "outcome": outcome,
            "reward": reward,
            "timestamp": time.time(),
        }
        self.history.append(episode)
        self.procedural.reinforce(str(action), reward)
        if reward > 0.5:
            existing = self.procedural.find_action(situation)
            if not existing:
                self.procedural.store(
                    name=str(action),
                    trigger=str(situation),
                    action=str(action),
                    strength=min(1.0, 0.5 + reward * 0.3),
                )
        return episode

    def best_action(self, situation):
        return self.procedural.find_action(situation)


class BayesianLearner:
    """Update beliefs based on evidence.
    Uses Bayes' theorem: P(H|E) = P(E|H) * P(H) / P(E)"""

    def __init__(self, semantic: SemanticMemory):
        self.semantic = semantic

    def observe(self, observation, related_beliefs=None):
        """Process an observation and update relevant beliefs."""
        updates = []
        if related_beliefs:
            beliefs_to_update = related_beliefs
        else:
            results = self.semantic.recall(str(observation), limit=10)
            beliefs_to_update = [r["data"]["proposition"]
                                for r in results if r["type"] == "belief"]

        for proposition in beliefs_to_update:
            obs_lower = str(observation).lower()
            prop_lower = str(proposition).lower()
            if any(w in obs_lower for w in prop_lower.split()):
                lr = 2.0
            else:
                lr = 0.5
            belief = self.semantic.update_belief(proposition, observation, lr)
            updates.append({
                "belief": proposition,
                "prior": belief["evidence"][-1]["prior"],
                "posterior": belief["confidence"],
                "evidence": observation,
            })
        return updates

    def current_belief(self, proposition):
        return self.semantic.get_belief(proposition)


class PatternExtractor:
    """Find recurring patterns in experience and generalize them into rules."""

    def __init__(self, episodic: EpisodicMemory, semantic: SemanticMemory):
        self.episodic = episodic
        self.semantic = semantic
        self.min_occurrences = 3

    def extract(self, focus=None):
        """Scan episodic memory for patterns and create semantic rules."""
        patterns = self.episodic.find_patterns(
            query=focus, min_occurrences=self.min_occurrences)

        new_rules = []
        for pattern in patterns:
            examples = pattern["examples"]
            if len(examples) < 2:
                continue
            conditions = []
            consequences = []
            for ep in examples:
                if ep.context.get("cause"):
                    conditions.append(str(ep.context["cause"]))
                if ep.context.get("outcome"):
                    consequences.append(str(ep.context["outcome"]))

            if conditions and consequences:
                from collections import Counter
                common_cond = Counter(conditions).most_common(1)[0][0]
                common_cons = Counter(consequences).most_common(1)[0][0]
                rule = self.semantic.store_rule(
                    condition=common_cond,
                    consequence=common_cons,
                    confidence=pattern["avg_importance"],
                    source="pattern_extraction",
                )
                new_rules.append(rule)

        return new_rules

    def find_associations(self, concept, min_strength=0.3):
        """Find concepts frequently associated with the given concept."""
        episodes = self.episodic.recall(str(concept), limit=50)
        co_occurring: dict[str, int] = {}
        for ep in episodes:
            for tag in ep.tags:
                if tag != str(concept):
                    co_occurring[tag] = co_occurring.get(tag, 0) + 1
        total = len(episodes) or 1
        associations = [
            {"concept": tag, "strength": count / total}
            for tag, count in co_occurring.items()
            if count / total >= min_strength
        ]
        associations.sort(key=lambda a: -a["strength"])
        return associations


class LearningEngine:
    """Combined learning: reinforcement + bayesian + pattern extraction."""

    def __init__(self, working: WorkingMemory, episodic: EpisodicMemory,
                 semantic: SemanticMemory, procedural: ProceduralMemory):
        self.working = working
        self.episodic = episodic
        self.semantic = semantic
        self.procedural = procedural
        self.reinforcement = ReinforcementLearner(procedural)
        self.bayesian = BayesianLearner(semantic)
        self.patterns = PatternExtractor(episodic, semantic)

    def process_experience(self, event):
        """Learn from a single experience using all three mechanisms."""
        results = {"reinforcement": None, "bayesian": None, "patterns": None}

        situation = event.get("situation", "")
        action = event.get("action", "")
        outcome = event.get("outcome", "")
        reward = event.get("reward", 0.0)
        observation = event.get("observation", "")

        if action and reward != 0:
            results["reinforcement"] = self.reinforcement.record(
                situation, action, outcome, reward)

        if observation:
            results["bayesian"] = self.bayesian.observe(observation)

        tags = event.get("tags", [])
        if isinstance(action, str) and action:
            tags.append(action)
        self.episodic.store(
            content=event,
            importance=abs(reward) if reward else 0.5,
            tags=tags,
            context={"cause": action, "outcome": outcome},
            emotional_weight=abs(reward) * 0.5,
        )

        return results

    def consolidate(self):
        """Run pattern extraction and strengthen important memories."""
        new_rules = self.patterns.extract()
        return {"new_rules": len(new_rules), "rules": new_rules}

    def __repr__(self):
        return "LearningEngine(reinforcement + bayesian + patterns)"
