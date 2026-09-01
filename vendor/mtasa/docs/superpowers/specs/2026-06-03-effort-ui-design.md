# Reasoning Effort UI expansion design

## Goal

Expand the frontend `Reasoning Effort` control so `aliyun` and `openrouter` expose the richer effort tiers the user requested, while keeping provider-specific behavior accurate and avoiding unsupported options on other providers.

## Current state

- `frontend/index.html` hardcodes `disable`, `low`, `high`.
- `fool/llm_client.py` only accepts `("disable", "low", "high")`.
- `openrouter` and `aliyun` should share the same expanded UI behavior.
- The UI must remain provider-aware rather than showing every effort tier for every provider.

## Options considered

### Option 1: Provider-aware dropdowns (chosen)

Render `Reasoning Effort` options from the selected provider/profile instead of a single hardcoded list.

Pros:
- UI matches actual provider capability.
- Avoids surfacing values that will be ignored or downgraded.
- Keeps future provider-specific expansion straightforward.

Cons:
- Requires synchronized frontend and backend updates.

### Option 2: Global superset dropdown

Show `disable`, `low`, `medium`, `high`, `xhigh` for every provider.

Pros:
- Smallest frontend change.

Cons:
- Misleading for providers that do not support those tiers.
- Encourages invalid or no-op selections.

### Option 3: Partial expansion

Add `medium` and `xhigh` only in the UI, but keep backend behavior narrow.

Pros:
- Minimal code churn.

Cons:
- Creates a UI/backend mismatch.
- Would silently coerce or ignore user selections.

## Approved design

### Frontend behavior

Replace the fixed `Reasoning Effort` `<select>` options with provider-aware rendering.

Target UI sets:

- `openrouter`: `disable`, `low`, `medium`, `high`, `xhigh`
- `aliyun`: `disable`, `low`, `medium`, `high`, `xhigh`
- `deepseek`: keep the currently safe subset unless backend mapping is intentionally expanded in the same change
- other providers: keep the current conservative subset

The rendered select should refresh when:

- auto-discovered profile changes
- manual API type changes
- saved config is hydrated into the form

If a previously saved value is not valid for the currently selected provider, the UI should fall back to that provider's default tier rather than leaving an invalid hidden value behind.

### Backend / payload behavior

Expand the accepted `effort_level` enum in `fool/llm_client.py` to include at least:

- `disable`
- `low`
- `medium`
- `high`
- `xhigh`

Provider mapping rules:

- `openrouter`: pass through named reasoning effort tiers; `disable` remains the unified off switch
- `aliyun`: match `openrouter` behavior for accepted values and request shaping in MTASA
- unsupported providers: continue ignoring `effort_level` rather than failing requests

If a provider-specific mapping needs to reject a value later, that rejection should happen by provider-specific normalization rather than leaving frontend/backend enums inconsistent.

### Documentation

Update `README.md` so it clearly distinguishes:

- upstream possible effort tiers
- MTASA UI-exposed tiers per provider

The Aliyun/OpenRouter section should explicitly mention that both now expose the expanded set:

- `disable`
- `low`
- `medium`
- `high`
- `xhigh`

### Testing

Add focused regression coverage for:

1. provider-aware frontend rendering or its source-of-truth config
2. accepted backend effort enum expansion
3. Aliyun/OpenRouter payload mapping for `medium` and `xhigh`
4. fallback behavior when a saved effort is invalid for the current provider

## Non-goals

- Reworking the overall API provider UX
- Adding new providers
- Expanding every provider to the same effort tiers

## Implementation notes

- Keep the change surgical: reuse existing profile/model rendering patterns in `frontend/app.js`.
- Preserve the current `.zshrc`-first Aliyun workflow; this design does not reintroduce manual Aliyun shortcut profiles.
- Do not claim provider support beyond what MTASA explicitly maps and tests.
