"""Tests for the Nova memory systems."""
import pytest
from nova.runtime.memory import (
    WorkingMemory, EpisodicMemory, SemanticMemory, ProceduralMemory, MemoryItem
)


class TestWorkingMemory:
    def test_hold_and_retrieve(self):
        wm = WorkingMemory(capacity=5)
        wm.hold(content="hello", importance=0.5)
        assert len(wm) == 1
        items = wm.contents()
        assert items[0].content == "hello"

    def test_capacity_limit(self):
        wm = WorkingMemory(capacity=3)
        for i in range(5):
            wm.hold(content=f"item{i}", importance=float(i) / 5)
        assert len(wm) <= 3

    def test_find(self):
        wm = WorkingMemory(capacity=7)
        wm.hold(content="the cat sat", importance=0.5)
        wm.hold(content="the dog ran", importance=0.5)
        results = wm.find("cat")
        assert len(results) >= 1

    def test_release(self):
        wm = WorkingMemory(capacity=7)
        wm.hold(content="data", importance=0.5)
        assert len(wm) == 1
        item = wm.contents()[0]
        wm.release(item)
        assert len(wm) == 0


class TestEpisodicMemory:
    def test_store_and_recall(self):
        em = EpisodicMemory(max_episodes=100)
        em.store(content="I saw a bird", importance=0.7, tags=["nature"])
        results = em.recall("bird", limit=5)
        assert len(results) >= 1

    def test_recent(self):
        em = EpisodicMemory()
        for i in range(10):
            em.store(content=f"event{i}", importance=0.5)
        recent = em.recent(3)
        assert len(recent) == 3

    def test_importance_filter(self):
        em = EpisodicMemory()
        em.store(content="trivial", importance=0.1)
        em.store(content="important", importance=0.9)
        results = em.recall("", limit=10)
        assert len(results) >= 1


class TestSemanticMemory:
    def test_store_fact(self):
        sm = SemanticMemory()
        sm.store_fact("Earth", "orbits", "Sun")
        results = sm.recall("Earth", limit=5)
        assert len(results) > 0

    def test_store_belief(self):
        sm = SemanticMemory()
        sm.store_belief("sky is blue", 0.9)
        belief = sm.get_belief("sky is blue")
        assert belief is not None
        assert belief["confidence"] == 0.9

    def test_update_belief(self):
        sm = SemanticMemory()
        sm.store_belief("hypothesis", 0.5)
        sm.update_belief("hypothesis", "supporting evidence", likelihood_ratio=2.0)
        belief = sm.get_belief("hypothesis")
        assert belief["confidence"] > 0.5

    def test_store_rule(self):
        sm = SemanticMemory()
        sm.store_rule("if rain", "then wet", confidence=0.8)
        rules = sm.matching_rules("rain")
        assert len(rules) >= 1

    def test_get_related(self):
        sm = SemanticMemory()
        sm.store_fact("A", "causes", "B")
        sm.store_fact("A", "causes", "C")
        related = sm.get_related("A")
        assert len(related) >= 2


class TestProceduralMemory:
    def test_store_and_find(self):
        pm = ProceduralMemory()
        pm.store(name="greet", trigger="hello", action="say hi", strength=0.8)
        action = pm.find_action("hello")
        assert action is not None
        assert action["name"] == "greet"

    def test_reinforce(self):
        pm = ProceduralMemory()
        pm.store(name="dodge", trigger="danger", action="move", strength=0.5)
        pm.reinforce("dodge", reward=0.8)
        action = pm.find_action("danger")
        assert action["strength"] > 0.5

    def test_reflex(self):
        pm = ProceduralMemory()
        pm.add_reflex("pain", "withdraw")
        reflex = pm.check_reflexes("pain")
        assert reflex is not None
        assert reflex["action"] == "withdraw"
