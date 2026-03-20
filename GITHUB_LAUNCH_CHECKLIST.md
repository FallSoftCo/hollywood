# GitHub Launch Checklist

This checklist is for publishing the coordinated `FallSoftCo` GitHub setup:

- `FallSoftCo/hollywood`
- `FallSoftCo/losangelex`

It is intentionally practical and ordered.

## 1. Hollywood repository

Current local state:

- git repo initialized
- branch: `main`
- release-prep commits present
- tests and basic compile check passing locally

Before push:

- choose the final GitHub repository name
- add the `FallSoftCo` remote
- push `main`
- verify the default branch is `main`
- enable Actions so the Python test workflow runs
- confirm the README renders correctly on GitHub

Suggested commands:

```bash
cd /home/ai/Development/hollywood
git remote add origin git@github.com:FallSoftCo/hollywood.git
git push -u origin main
```

After push:

- create the first GitHub release when ready
- copy the local project description into the repository description
- add repository topics such as `agents`, `cli`, `coordination`, `python`

## 2. LosangElex repository

Current local state:

- branch contains committed Hollywood design/docs work
- remaining runtime integration changes are still under validation/commit sequencing

Before push:

- finish validation for the Hollywood runtime changes
- commit the dirty runtime slice cleanly
- verify the branch history is understandable
- make sure the Hollywood docs and runtime commits are grouped sensibly
- decide whether to publish directly from the current branch or from a cleaned branch

Do not announce `FallSoftCo/losangelex` until:

- runtime validation has completed
- the worktree is clean
- the intended default branch and branch strategy are decided

## 3. Cross-repo coordination

Before public announcement:

- verify links between the two repositories
- make sure `losangelex` is the primary "start here" entrypoint
- make sure `hollywood` README links to the coordinated install guide
- make sure `losangelex` docs link back to `hollywood`
- confirm the environment variables match across both doc sets:
  - `HOLLYWOOD_AUTO_ATTACH`
  - `HOLLYWOOD_URL`
  - `HOLLYWOOD_ROOM`
  - `HOLLYWOOD_ATTENTION_MODE`

## 4. Install verification

Run one clean end-to-end verification after both repos are pushed:

1. clone `FallSoftCo/hollywood`
2. install and start Hollywood
3. clone `FallSoftCo/losangelex`
4. build/run LosangElex with Hollywood auto-attach enabled
5. verify two sessions can coordinate through the room

Success criteria:

- Hollywood health endpoint responds
- LosangElex starts with Hollywood attached
- inbound room traffic is visible
- mentions are surfaced correctly
- the documented commands work as written

## 5. Documentation minimum

Before launch, each repository should answer:

- what is this?
- how do I install it?
- how do I run it?
- how does it relate to the other repository?
- what is experimental versus stable?

Additionally, the pair of repositories must answer:

- which repository should a new user start from?
- what is required versus optional in the integrated setup?

## 6. Announcement bar

You are ready for a public GitHub launch when:

- `FallSoftCo/hollywood` is pushed and clean
- `FallSoftCo/losangelex` is pushed from a validated, clean branch
- the coordinated install flow has been tested once end-to-end
- both READMEs link to each other clearly
- at least one short demo transcript or screenshot exists

Until then, prefer calling the setup:

- prepared for publication
- nearing open-source release

and not:

- publicly released
- ready for broad adoption
