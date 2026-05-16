"""Nova Cognitive Memory Systems.

Four memory types modeled after human cognition:
- Working Memory: limited capacity, active processing (like RAM)
- Episodic Memory: autobiographical events with context (like personal history)
- Semantic Memory: facts, concepts, relationships (like a knowledge graph)
- Procedural Memory: learned behaviors and skills (like muscle memory)

Consolidation is continuous + importance-based.
"""

import time
import math
import hashlib
from dataclasses import dataclass, field
from typing import Any


@dataclass
class MemoryItem:
    content: Any
    timestamp: float = field(default_factory=time.time)
    importance: float = 0.5
    access_count: int = 0
    last_accessed: float = field(default_factory=time.time)
    tags: list[str] = field(default_factory=list)
    context: dict = field(default_factory=dict)
    emotional_weight: float = 0.0
    id: str = ""

    def __post_init__(self):
        if not self.id:
            raw = f"{self.content}{self.timestamp}"
            self.id = hashlib.md5(raw.encode() if isinstance(raw, str) else str(raw).encode()).hexdigest()[:12]

    def activation(self, current_time=None):
        """Memory activation level — higher means more retrievable.
        Based on ACT-R: decays with time, boosted by access frequency and importance."""
        now = current_time or time.time()
        recency = 1.0 / (1.0 + (now - self.last_accessed))
        frequency = math.log(1 + self.access_count)
        return (recency * 0.4 + frequency * 0.3 +
                self.importance * 0.2 + self.emotional_weight * 0.1)

    def touch(self):
        self.access_count += 1
        self.last_accessed = time.time()


class WorkingMemory:
    """Short-term, limited capacity. Like a mental scratchpad.
    Only holds what you're actively thinking about."""

    def __init__(self, capacity=7):
        self.capacity = capacity
        self.slots: list[MemoryItem] = []
        self.focus: MemoryItem | None = None

    def hold(self, content, importance=0.5, tags=None, context=None):
        item = MemoryItem(
            content=content, importance=importance,
            tags=tags or [], context=context or {},
        )
        if len(self.slots) >= self.capacity:
            self._evict_least_important()
        self.slots.append(item)
        self.focus = item
        return item

    def release(self, item_or_id=None):
        if item_or_id is None:
            self.slots.clear()
            self.focus = None
            return
        target_id = item_or_id if isinstance(item_or_id, str) else item_or_id.id
        self.slots = [s for s in self.slots if s.id != target_id]
        if self.focus and self.focus.id == target_id:
            self.focus = self.slots[-1] if self.slots else None

    def contents(self):
        return list(self.slots)

    def snapshot(self):
        return {
            "items": [{"content": s.content, "importance": s.importance,
                        "tags": s.tags} for s in self.slots],
            "focus": self.focus.content if self.focus else None,
            "timestamp": time.time(),
        }

    def find(self, query):
        results = []
        for item in self.slots:
            score = self._match_score(item, query)
            if score > 0:
                item.touch()
                results.append((item, score))
        results.sort(key=lambda x: -x[1])
        return [r[0] for r in results]

    def _evict_least_important(self):
        if not self.slots:
            return
        least = min(self.slots, key=lambda s: s.activation())
        self.slots.remove(least)
        return least

    @staticmethod
    def _match_score(item, query):
        if isinstance(query, str):
            content_str = str(item.content).lower()
            query_lower = query.lower()
            if query_lower in content_str:
                return 1.0
            words = set(query_lower.split())
            content_words = set(content_str.split())
            overlap = words & content_words
            return len(overlap) / max(len(words), 1)
        return 0.0

    def __len__(self):
        return len(self.slots)

    def __repr__(self):
        return f"WorkingMemory({len(self.slots)}/{self.capacity})"


class EpisodicMemory:
    """Autobiographical memory — stores experiences as episodes.
    Each episode has: what happened, when, context, emotional weight, outcome."""

    def __init__(self, max_episodes=100000):
        self.max_episodes = max_episodes
        self.episodes: list[MemoryItem] = []

    def store(self, content, importance=0.5, tags=None, context=None,
              emotional_weight=0.0):
        item = MemoryItem(
            content=content, importance=importance,
            tags=tags or [], context=context or {},
            emotional_weight=emotional_weight,
        )
        self.episodes.append(item)
        if len(self.episodes) > self.max_episodes:
            self._consolidate()
        return item

    def recall(self, query, limit=5):
        scored = []
        for ep in self.episodes:
            score = self._relevance(ep, query)
            if score > 0:
                scored.append((ep, score))
        scored.sort(key=lambda x: -x[1])
        results = []
        for ep, score in scored[:limit]:
            ep.touch()
            results.append(ep)
        return results

    def similar(self, reference, limit=5):
        if isinstance(reference, MemoryItem):
            ref_content = str(reference.content)
        else:
            ref_content = str(reference)
        return self.recall(ref_content, limit)

    def recent(self, n=10):
        items = sorted(self.episodes, key=lambda e: -e.timestamp)[:n]
        return items

    def find_patterns(self, query=None, min_occurrences=3):
        """Find recurring patterns in episodes."""
        if not self.episodes:
            return []
        tag_counts: dict[str, int] = {}
        for ep in self.episodes:
            for tag in ep.tags:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1

        patterns = []
        for tag, count in tag_counts.items():
            if count >= min_occurrences:
                related = [ep for ep in self.episodes if tag in ep.tags]
                avg_importance = sum(e.importance for e in related) / len(related)
                patterns.append({
                    "pattern": tag,
                    "occurrences": count,
                    "avg_importance": avg_importance,
                    "examples": related[:3],
                })
        return patterns

    def _relevance(self, episode, query):
        if isinstance(query, str):
            content_str = str(episode.content).lower()
            query_lower = query.lower()
            text_match = 0.0
            if query_lower in content_str:
                text_match = 1.0
            else:
                words = set(query_lower.split())
                content_words = set(content_str.split())
                overlap = words & content_words
                text_match = len(overlap) / max(len(words), 1)
            tag_match = 0.0
            for tag in episode.tags:
                if query_lower in tag.lower():
                    tag_match = 0.5
                    break
            activation = episode.activation()
            return text_match * 0.5 + tag_match * 0.2 + activation * 0.3
        return 0.0

    def _consolidate(self):
        """Remove least important memories when at capacity."""
        self.episodes.sort(key=lambda e: e.activation(), reverse=True)
        self.episodes = self.episodes[:self.max_episodes]

    def __len__(self):
        return len(self.episodes)

    def __repr__(self):
        return f"EpisodicMemory({len(self.episodes)} episodes)"


class SemanticMemory:
    """Knowledge graph — facts, concepts, relationships, beliefs.
    Structured as a graph where nodes are concepts and edges are relationships."""

    def __init__(self):
        self.concepts: dict[str, dict] = {}
        self.relations: list[dict] = []
        self.beliefs: dict[str, dict] = {}
        self.rules: list[dict] = []

    def store_fact(self, subject, predicate, obj, confidence=1.0, source=None):
        for node in (subject, obj):
            if node not in self.concepts:
                self.concepts[node] = {
                    "name": node, "type": "concept",
                    "properties": {}, "created": time.time(),
                }
        relation = {
            "subject": subject, "predicate": predicate, "object": obj,
            "confidence": confidence, "source": source,
            "timestamp": time.time(),
        }
        self.relations.append(relation)
        return relation

    def store_belief(self, proposition, confidence=0.5, evidence=None):
        self.beliefs[proposition] = {
            "proposition": proposition,
            "confidence": confidence,
            "evidence": evidence or [],
            "timestamp": time.time(),
            "prior": confidence,
        }
        return self.beliefs[proposition]

    def update_belief(self, proposition, new_evidence, likelihood_ratio=2.0):
        """Bayesian belief update: P(H|E) ∝ P(E|H) * P(H)"""
        if proposition not in self.beliefs:
            self.store_belief(proposition, confidence=0.5)
        belief = self.beliefs[proposition]
        prior = belief["confidence"]
        posterior = (prior * likelihood_ratio) / (
            prior * likelihood_ratio + (1 - prior))
        posterior = max(0.01, min(0.99, posterior))
        belief["confidence"] = posterior
        belief["evidence"].append({
            "evidence": new_evidence,
            "timestamp": time.time(),
            "prior": prior,
            "posterior": posterior,
        })
        return belief

    def store_rule(self, condition, consequence, confidence=0.8, source=None):
        rule = {
            "if": condition, "then": consequence,
            "confidence": confidence, "source": source,
            "uses": 0, "timestamp": time.time(),
        }
        self.rules.append(rule)
        return rule

    def recall(self, query, limit=10):
        results = []
        query_lower = query.lower() if isinstance(query, str) else ""
        for name, concept in self.concepts.items():
            if query_lower in name.lower():
                results.append({"type": "concept", "data": concept,
                                "relevance": 1.0})
        for rel in self.relations:
            text = f"{rel['subject']} {rel['predicate']} {rel['object']}"
            if query_lower and query_lower in text.lower():
                results.append({"type": "relation", "data": rel,
                                "relevance": rel["confidence"]})
        for prop, belief in self.beliefs.items():
            if query_lower and query_lower in prop.lower():
                results.append({"type": "belief", "data": belief,
                                "relevance": belief["confidence"]})
        for rule in self.rules:
            text = f"{rule['if']} {rule['then']}"
            if query_lower and query_lower in text.lower():
                results.append({"type": "rule", "data": rule,
                                "relevance": rule["confidence"]})
        results.sort(key=lambda r: -r["relevance"])
        return results[:limit]

    def get_related(self, concept_name, depth=1):
        related = set()
        frontier = {concept_name}
        for _ in range(depth):
            next_frontier = set()
            for node in frontier:
                for rel in self.relations:
                    if rel["subject"] == node:
                        next_frontier.add(rel["object"])
                        related.add((node, rel["predicate"], rel["object"]))
                    if rel["object"] == node:
                        next_frontier.add(rel["subject"])
                        related.add((rel["subject"], rel["predicate"], node))
            frontier = next_frontier - {concept_name}
        return related

    def matching_rules(self, condition_query):
        matches = []
        query_lower = condition_query.lower() if isinstance(condition_query, str) else ""
        for rule in self.rules:
            cond_str = str(rule["if"]).lower()
            if query_lower and query_lower in cond_str:
                rule["uses"] += 1
                matches.append(rule)
        matches.sort(key=lambda r: -(r["confidence"] * math.log(1 + r["uses"])))
        return matches

    def get_belief(self, proposition):
        return self.beliefs.get(proposition)

    def concepts_count(self):
        return len(self.concepts)

    def __repr__(self):
        return (f"SemanticMemory({len(self.concepts)} concepts, "
                f"{len(self.relations)} relations, "
                f"{len(self.beliefs)} beliefs, {len(self.rules)} rules)")


class ProceduralMemory:
    """Skills and learned behaviors. Maps situation → action with success tracking.
    Updated via reinforcement: success increases strength, failure decreases it."""

    def __init__(self):
        self.procedures: dict[str, dict] = {}
        self.reflexes: list[dict] = []

    def store(self, name, trigger, action, strength=0.5):
        self.procedures[name] = {
            "name": name, "trigger": trigger, "action": action,
            "strength": strength, "successes": 0, "failures": 0,
            "last_used": None, "timestamp": time.time(),
        }
        return self.procedures[name]

    def reinforce(self, name, reward):
        """Update procedure strength based on outcome.
        reward > 0 = success, reward < 0 = failure."""
        if name not in self.procedures:
            return
        proc = self.procedures[name]
        lr = 0.1
        proc["strength"] = max(0.0, min(1.0,
            proc["strength"] + lr * reward))
        if reward > 0:
            proc["successes"] += 1
        else:
            proc["failures"] += 1
        proc["last_used"] = time.time()

    def find_action(self, situation):
        """Find the best action for a given situation."""
        candidates = []
        sit_lower = str(situation).lower()
        for name, proc in self.procedures.items():
            trigger_str = str(proc["trigger"]).lower()
            if sit_lower in trigger_str or trigger_str in sit_lower:
                candidates.append(proc)
        candidates.sort(key=lambda p: -p["strength"])
        return candidates[0] if candidates else None

    def add_reflex(self, trigger, action, priority=1.0):
        self.reflexes.append({
            "trigger": trigger, "action": action,
            "priority": priority, "timestamp": time.time(),
        })

    def check_reflexes(self, stimulus):
        """Check if any reflex fires for this stimulus."""
        stim_lower = str(stimulus).lower()
        for reflex in sorted(self.reflexes, key=lambda r: -r["priority"]):
            trigger_str = str(reflex["trigger"]).lower()
            if trigger_str in stim_lower or stim_lower in trigger_str:
                return reflex
        return None

    def all_procedures(self):
        return list(self.procedures.values())

    def __repr__(self):
        return (f"ProceduralMemory({len(self.procedures)} procedures, "
                f"{len(self.reflexes)} reflexes)")
