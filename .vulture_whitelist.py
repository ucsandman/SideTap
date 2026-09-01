# Vulture whitelist for the pre-commit hook (~/.claude/git-hooks/pre-commit).
# The hook scans ONLY the staged files, so when wda_client.py is staged alone
# vulture cannot see its callers in helpers.py, viewer.py and the tests, and
# flags the public client API as dead. Vulture parses this file (never runs
# it): a bare attribute reference marks that name as used.
from phone_harness.wda_client import WDAClient, redact_actions

redact_actions
WDAClient
WDAClient.screenshot
WDAClient.window_size
WDAClient.tap
WDAClient.swipe
WDAClient.type_text
WDAClient.home
