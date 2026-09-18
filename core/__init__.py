"""tkt core — provider-independent plumbing."""

# The one place tkt's version is written. Bump it when cutting a release;
# `tkt --version` appends the checkout's commit, so unreleased builds are still
# identifiable. Nothing else may restate this literal (tests/test_version.py).
__version__ = "0.1.0"
