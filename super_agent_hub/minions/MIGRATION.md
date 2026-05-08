# 🔄 Migration Guide: Claude/Perplexity → Kimi K2.6

## What Changed

| Before | After |
|--------|-------|
| `MinionClaude` + `MinionPerplexity` | `MinionKimi` (single minion) |
| `ANTHROPIC_API_KEY` | `MOONSHOT_API_KEY` |
| `PERPLEXITY_API_KEY` | `MOONSHOT_API_KEY` |
| `claude-3-5-sonnet-20241022` | `kimi-k2.6` |
| `sonar-pro` | `kimi-k2.6` |
| `pip install anthropic` | REMOVED (not needed) |

## Environment Variables

```bash
# Remove these:
# export ANTHROPIC_API_KEY="sk-ant-..."
# export PERPLEXITY_API_KEY="pplx-..."

# Add this:
export MOONSHOT_API_KEY="sk-your-moonshot-key"
```

## Code Changes

### Before (Claude + Perplexity)
```python
from super_agent_hub.minions import MinionClaude, MinionPerplexity

claude = MinionClaude()
claude.configure(api_key="sk-ant-...")

perplexity = MinionPerplexity()
perplexity.configure(api_key="pplx-...")
```

### After (Kimi)
```python
from super_agent_hub.minions import MinionKimi

kimi = MinionKimi()
kimi.configure(
    api_key="sk-...",
    model="kimi-k2.6",  # or "kimi-k2.5", "kimi-k2-thinking"
    mode="auto"         # "code", "research", or "auto"
)
```

## API Endpoints

All endpoints stay the same. Only the internal agent changed:
- `GET /api/agents/status` → now shows `minion_kimi`
- `POST /api/tasks` → routes to Kimi instead of Claude/Perplexity

## Pool Usage

```python
from minions.pool import MinionPool

pool = MinionPool()
# Uses MOONSHOT_API_KEY from env

research = pool.ask_researcher("Compare vector databases")
code = pool.ask_coder("Build FastAPI auth", context=research)
```

## Get API Key

1. Go to https://platform.kimi.ai
2. Create an API key (starts with `sk-`)
3. Set `MOONSHOT_API_KEY` in your environment
