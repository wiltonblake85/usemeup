# Skills copied from ECC

These fifteen skill folders, and `.claude/commands/plan-prd.md`, are
unmodified copies from ECC (https://github.com/affaan-m/ECC), version
2.2.3, commit `ef648e0`, under the MIT license in `ECC-LICENSE`.

They are copied rather than enabled as a plugin because cloud sessions
do not install plugins a repo enables in `.claude/settings.json`, while
they do load `.claude/skills/` and `.claude/commands/`. The full plugin
also adds about 45,000 tokens to every session; this set adds about 1,500.

| Stage | Skills |
|---|---|
| Shape the idea | `product-lens`, `market-research`, `/plan-prd` |
| Build | `tdd-workflow` |
| Test | `verification-loop`, `eval-harness`, `ai-regression-testing`, `production-audit` |
| Run an AI product | `cost-aware-llm-pipeline` |
| Promo video | `remotion-video-creation`, `ui-demo` |
| Find buyers | `lead-intelligence`, `social-graph-ranker`, `brand-voice`, `x-api` |
| Remember | `growth-log` |

`lead-intelligence` scores warm paths with `social-graph-ranker`, so the
two travel together. `tdd-workflow` and `eval-harness` mention helper
scripts that live in the ECC repo (`scripts/setup-package-manager.js`,
`scripts/eval-harness.js`); they are optional and not copied here.
`/plan-prd` hands off to ECC's `/plan`, which is not copied; plan in
Claude Code's own plan mode instead.

Left out on purpose: skills for specific languages, frameworks and
unrelated industries; ECC's own hook, memory and orchestration plumbing;
`deep-research` and `exa-search`, which need Exa or Firecrawl connectors;
and `security-review`, which Claude Code already ships under that name.

To refresh, clone ECC and copy the same files over these:

```
git clone --depth 1 https://github.com/affaan-m/ECC /tmp/ecc
for s in product-lens market-research tdd-workflow verification-loop \
  eval-harness ai-regression-testing production-audit \
  cost-aware-llm-pipeline remotion-video-creation ui-demo \
  lead-intelligence social-graph-ranker brand-voice x-api growth-log; do
  rm -rf .claude/skills/$s && cp -R /tmp/ecc/skills/$s .claude/skills/
done
cp /tmp/ecc/commands/plan-prd.md .claude/commands/
cp /tmp/ecc/LICENSE .claude/skills/ECC-LICENSE
```

Then update the version and commit above.
