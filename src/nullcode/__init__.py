"""NullCode: the coding-workflow subsystem of the NullBrain agent platform.

Subpackages
-----------
core      Inference/verification primitives and the queue worker entry point.
repo      Local-repository workflow profiles built on ``core``.
gradle    Offline Gradle/JUnit verification profile and its approved templates.
publish   Acceptance checking and explicit GitHub draft-PR delivery.
fixtures  Generators for the local Git repositories used by the smoke tests.

Runtime state (job directories, the workflow database, generated repositories)
lives outside this package; see ``docs/ARCHITECTURE.md``.
"""

__all__ = ["core", "repo", "gradle", "publish", "fixtures"]
