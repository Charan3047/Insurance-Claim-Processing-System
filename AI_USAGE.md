# AI assistance and prompt record

This project was developed with substantial generative-AI assistance. AI was
used to interpret the challenge, design and implement the synthetic data
generator and local workflow application, write and revise documentation, and
run/review validation and tests. The candidate supplied the challenge
requirements, directed the work, requested audits and corrections, and should
review the code and results personally before presenting the project.

This disclosure is intended to be candid: the implementation was not written
independently by the candidate. The candidate should be prepared to explain the
workflow, data model, rules, test results, trade-offs, and limitations in their
own words. Do not claim personal implementation or review steps that you did
not perform.

## Prompt history (condensed)

The following summarizes the material prompts used during the work. It omits
repeated wording but preserves the requests and scope:

1. The candidate described a 72-hour project-delivery round and asked for help
   preparing a successful submission.
2. The candidate supplied the insurance-claims challenge specification and
   asked for a line-by-line comparison with the project.
3. The candidate requested a fresh audit against the full specification,
   including running the generator, validator, and tests and checking
   GitHub-readiness.
4. After reviewing the gaps, the candidate requested that the project be made
   fully compliant.
5. The candidate asked for a beginner-friendly explanation of what the
   project contains and what it is intended to demonstrate.
6. The candidate supplied the submission-guidelines PDF and asked whether the
   project was ready under those guidelines.
7. The candidate requested that the project materials be made submission-ready
   so they could push the repository to GitHub.

## AI-assisted work and human review

- AI assistance covered requirements analysis, implementation, code changes,
  documentation, test execution, and packaging.
- The project includes automated tests and a validator so reviewers can repeat
  the checks locally and in GitHub Actions.
- Before submission, the candidate should run the commands in `README.md`,
  inspect the resulting output, read `DESIGN_NOTES.md` and
  `PRESENTATION_GUIDE.md`, and independently confirm they can explain the
  behavior. This file does not assert that this independent review has already
  happened.
- AI-generated code and synthetic outputs may contain mistakes. The test and
  validation results are evidence for this version, not a guarantee of
  production suitability.
