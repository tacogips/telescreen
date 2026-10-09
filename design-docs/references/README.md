# Design References

This directory contains reference materials for system design and implementation.

## External References

| Name | URL | Description |
|------|-----|-------------|
| Python Documentation | https://docs.python.org/3/ | Official Python language and standard library documentation |
| Python Packaging User Guide | https://packaging.python.org/ | Python packaging standards and project configuration guidance |
| uv Documentation | https://docs.astral.sh/uv/ | uv package and project management documentation |
| Ruff Documentation | https://docs.astral.sh/ruff/ | Python linting and formatting documentation |
| ty Documentation | https://docs.astral.sh/ty/ | Python type checker documentation |
| pytest Documentation | https://docs.pytest.org/ | Python testing framework documentation |
| FastAPI | https://fastapi.tiangolo.com/ | Bureau web framework (pydantic dataclass bodies) |
| SQLAlchemy 2.0 | https://docs.sqlalchemy.org/en/20/ | Async ORM, `MappedAsDataclass`, `with_for_update(skip_locked=True)` |
| Alembic | https://alembic.sqlalchemy.org/ | Migrations |
| Meilisearch API | https://www.meilisearch.com/docs/reference/api/overview | Search REST API used over httpx |
| Model Context Protocol Python SDK | https://github.com/modelcontextprotocol/python-sdk | Assistant MCP server |
| Anthropic API | https://docs.anthropic.com/ | Managed LLM detector and chat |
| Slack request signing | https://api.slack.com/authentication/verifying-requests-from-slack | Slack Events API verification |
| GitHub webhook validation | https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries | `X-Hub-Signature-256` |
| Google Drive push notifications | https://developers.google.com/drive/api/guides/push | `changes.watch` channels |
| n8n Webhook and Crypto nodes | https://docs.n8n.io/integrations/builtin/core-nodes/ | Push-mode police example |
| Kestra workers | https://kestra.io/docs/architecture | Inspiration for pull-mode police |
| riela | https://github.com/tacogips/riela | Workflow concepts (nodes, manager/worker, branching, loops) |
| JSON Schema 2020-12 | https://json-schema.org/draft/2020-12 | Police contract schemas |
| ai4privacy pii-masking-400k | https://huggingface.co/datasets/ai4privacy/pii-masking-400k | Synthetic multilingual PII dataset with span labels (detector evaluation; no Japanese) |
| ai4privacy pii-masking-300k | https://huggingface.co/datasets/ai4privacy/pii-masking-300k | Earlier synthetic PII masking dataset (detector evaluation) |
| Nemotron-Personas-Japan | https://huggingface.co/datasets/nvidia/Nemotron-Personas-Japan | NVIDIA synthetic Japanese personas, CC BY 4.0 (names/profiles for test templates) |
| presidio-evaluator | https://pypi.org/project/presidio-evaluator/ | Template-based synthetic PII sentence generation with labels |
| Faker | https://faker.readthedocs.io/ | Fake value providers (`ja_JP`, `en_US`) for filler data |
| RFC 2606 / RFC 6761 | https://www.rfc-editor.org/rfc/rfc6761 | Reserved example and `.invalid` domains for fictitious email addresses |

## Reference Documents

Reference documents should be organized by topic:

```
references/
├── README.md              # This index file
├── python/                # Python patterns and practices
└── <topic>/               # Other topic-specific references
```

## Adding References

When adding new reference materials:

1. Create a topic directory if it does not exist
2. Add reference documents with clear naming
3. Update this README.md with the reference entry
