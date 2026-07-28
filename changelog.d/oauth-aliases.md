---
type: feat
scope: cli
---
模型別名（sonnet/haiku/opus/fable）與 OAuth bearer 認證——run/versus 的 --model 可只打別名；AnthropicAdapter 支援 auth_token（Authorization: Bearer + oauth beta header），使用者可用 ant auth login 的 Claude 帳號而不必管 API key。
