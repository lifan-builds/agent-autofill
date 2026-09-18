# Codex Skill Authoring Evidence

Source: [Build skills](https://learn.chatgpt.com/docs/build-skills), official
OpenAI documentation, accessed 2026-09-16.

Relevant findings for the form-record suggestion deliverable:

- A skill is a directory with a required `SKILL.md`; scripts, references,
  assets, and `agents/openai.yaml` are optional.
- `SKILL.md` requires `name` and `description` frontmatter.
- Codex can select a skill implicitly when the request matches its description,
  so the description must state a concise, discriminating scope.
- Repository-local skills are discovered under `$REPO_ROOT/.agents/skills`
  (and corresponding `.agents/skills` directories between the working
  directory and repository root).
- Official best practices are to keep each skill focused on one job, prefer
  instructions over scripts unless deterministic behavior requires code, use
  explicit inputs/outputs, and test realistic prompts against the description.
- Plugin packaging is appropriate for broader distribution, but a direct
  repository-local skill is the right first scope for this project.
