# Private native candidate builds

This repository hosts only the generic workflow and synthetic privacy tests. Product source remains in the private repository. This workflow does not publish releases, install applications, change signing identities, or use paid/larger runners.

`private-platform-build.yml` accepts an exact open private PR head, or the exact head/merge commit of a merged private PR that is still an ancestor of private main, and an **age public recipient**. Closed unmerged PRs, moved open heads, unrelated commits and removed merged commits are rejected. Never provide an age secret identity or signing key as an input. Admission requires the repository owner and the public default branch, reuses the existing `private-validation` environment, and keeps checkout credentials out of child build processes.

The matrix uses standard hosted `macos-15` (iOS simulator and arm64 desktop), `macos-15-intel` (x64 desktop), and `ubuntu-24.04` (x64 desktop) runners. Each checks its actual architecture. All dependency preparation occurs on disposable runners. Platform scripts in the admitted private commit remain authoritative; private build output is redirected to a local file, not the public log. Each job has a 90-minute limit; the build process group is terminated after 75 minutes.

Only `private-platform.tar.age` is uploaded, with three-day retention. It contains encrypted diagnostics and candidate artifacts; decryption requires the local secret identity, which is never sent to GitHub. A failed encryption removes partial ciphertext and the temporary plaintext archive. No plaintext upload fallback exists. Build success, successful encrypted delivery, signing, simulator tests, installation and store distribution are separate facts. iOS simulator apps are not installable phone IPAs.

The workflow must first be reviewed and merged into the default branch before manual dispatch. The owner has authorized this native build iteration and related PR merges. Successful private candidate status requires the native build, encryption and artifact upload all to succeed. A failed upload cannot report successful delivery. The existing full-suite validation matrix remains independent of native candidate status.

Synthetic checks: `python scripts/test_platform_workflow.py` (requires PyYAML). They check workflow Python syntax, the exact upload path, and encryption success/failure cleanup; they do not prove product compilation, real cryptography, native runtime functionality or device acceptance.
