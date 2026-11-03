# AI Provider Configuration

W-BOT uses Google Gemini as its default chat provider.

## Recommended low-cost setup

Set these environment variables in Render (or your local `.env`):

```env
PRIMARY_PROVIDER=gemini
GEMINI_MODEL=gemini-3.5-flash-lite
GEMINI_API_KEY=your_google_ai_studio_api_key
AI_FALLBACK_ENABLED=false
```

The Gemini Developer API currently lists Gemini 3.5 Flash-Lite as a stable, cost-efficient model with a free tier. Free-tier quotas are limited and can change. Check the project's active limits in Google AI Studio.

## Prevent unexpected OpenRouter usage

`AI_FALLBACK_ENABLED=false` is the default. If Gemini returns a quota, rate-limit, or service error, W-BOT returns a temporary-unavailable message instead of automatically sending the request to OpenRouter.

Only set `AI_FALLBACK_ENABLED=true` if you deliberately want OpenRouter fallback and understand its model pricing. Setting `PRIMARY_PROVIDER=openrouter` explicitly also routes requests directly to OpenRouter.

Keep API keys in Render environment variables or a local `.env` file. Never commit real keys to GitHub.

## Privacy note

Google's free-tier terms may allow submitted prompts and responses to be used to improve its services. Avoid sending passwords, private identifiers, or other sensitive WhatsApp content to the free API.
