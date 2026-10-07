# Session Handoff

## Current State
AI toolkit installed on 2026-10-04; evidence in docs/AI_TOOLKIT.md and F:/AI_Agent_Toolkit/mcp-verification.json.

## Next Steps
Read AGENTS.md, README.md, feature_list.json and progress.md. Select one concrete unfinished feature from the user's request. Run ./init.sh before and after changing code. Update feature state and progress with actual evidence.

## Known Limitations
NotebookLM has no authenticated profile yet. JEV/Clef original weights are not installed; native SpikingBrain dependencies require a compatible Linux GPU environment. Optional MAI and AgentOps cloud integrations remain disabled until configured.

## Clean Restart
No new automatic model loading or paid agent runs. Existing app launchers remain the startup path. Preserve all existing datasets, voices, weights, keys and uncommitted changes.
