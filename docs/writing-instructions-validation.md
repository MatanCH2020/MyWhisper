# Combined writing instructions — 1.16.4

Validated on Windows on 2026-10-31 with the account-selected `gpt-5.6-luna`.
Only synthetic texts were sent through the real `TextEditor`/Responses path.
The regular app was stopped during the benchmark to avoid concurrent refresh-token
rotation. Saved mode and model were not changed.

## Scope and evidence

- Six cases: WhatsApp with emoji throughout, email with an instruction at the end,
  one emoji per line requested mid-dictation, cancelling an emoji request,
  quoting an emoji request verbatim, and plain dictation without an emoji request.
- The original prompt already handled all six sample texts on manual review.
  The user's specific omission was **not reproduced**. The initial automated
  checker missed the alarm-clock emoji U+23F0; its fixture range was corrected.
- The strengthened prompt passed all six cases in two rounds (12/12). Manual
  review confirmed emoji across content, all three lines covered in the per-line
  case, and no leaked formatting instructions. Greetings and email sign-offs
  are not treated as content paragraphs by the prompt.
- Measured completed-request latency: median 2,781 ms, maximum 4,719 ms. This is a
  small sample, not a performance or reliability guarantee.
- A mocked transport regression verifies that emoji survive the completed response,
  combined instructions reach the request intact, and a subsequent dictation has
  no previous-message context. It does not measure model compliance.
- `pip check` and `scripts/verify_install.py` passed. Native delivery smoke testing
  could not establish foreground focus in this automation session and failed its
  same-window assertion; the subsequent UI driver reported unavailable geometry.
  Native paste was therefore not revalidated. No microphone/ASR test was performed
  for this prompt-only change, and no user's recorded audio was stored or replayed.
- Local full suite: 361 tests, 13 skipped, one timing-sensitive failure in the
  existing clipboard-window activation retry test (one callback instead of two).
  That test and all 13 editor tests passed together on a focused rerun. Release
  publication also requires the full suite to pass in Windows CI.

## Reproduce the model check

Close MyWhisper first. With cloud mode already explicitly enabled, run:

```powershell
.\.venv\Scripts\python scripts/benchmark_chatgpt.py --output output/emoji-check.json --case emoji-whatsapp --case emoji-email-end --case emoji-middle --case emoji-cancel --case emoji-quoted --case emoji-plain --rounds 2
```

The report contains only these synthetic inputs/outputs. Fragment and emoji-presence
checks are coarse; read the output to assess meaning and distribution. Do not treat
a passing sample as proof that every spoken instruction will be followed.

## WhatsApp formatting — 1.16.5

Validated on 2026-10-31 using the same account-selected `gpt-5.6-luna` and synthetic
text only. Six new fixtures cover WhatsApp bold/italic/strikethrough/quote syntax,
three paragraphs, requested sparse emoji, explicit plain text, a WhatsApp mention
that is not a writing instruction, and a bullet list with a literal URL.

Initial live probes caught a doubled Markdown strikethrough delimiter, unsolicited
emoji, and an omitted/translated WhatsApp mention. The final instruction includes
explicit single-delimiter examples, paired examples with/without emoji permission,
and preservation of application names when they are dictated content.

The final run passed 12/12 checks (six cases, two rounds). Outputs were read for
meaning, spacing and marker placement as well as checked mechanically. Completed
request latency was median 3,078.5 ms and maximum 3,922 ms in this small sample.
This is prompt guidance, not a deterministic formatter or a guarantee of model
compliance. The app does not rewrite arbitrary Markdown using regular expressions.

Local validation: the full 362-test suite passed with 13 platform skips. All 14
editor tests also passed after the final prompt revision. `pip check`, installation
verification and release sync passed. The transport regression checks exact marker
and paragraph preservation through the completed response; model quality is tested
by the separate live benchmark. The existing clipboard activation retry test now
waits up to one second for its observable second callback rather than assuming
a loaded CI event loop always delivers it within 160 ms; its exact-count, alert
and timer assertions remain intact.

Syntax reference: [WhatsApp Help Center](https://faq.whatsapp.com/539178204879377/).
Its desktop guidance says newer list/quote formatting is available on Web and Mac.
Actual rendering in WhatsApp for Windows, microphone recognition and native paste
were not revalidated for this prompt-only change. No WhatsApp messages were sent.

```powershell
.\.venv\Scripts\python scripts/benchmark_chatgpt.py --output output/whatsapp-check.json --case wa-styles --case wa-paragraphs --case wa-emoji --case wa-plain --case wa-mentioned --case wa-list-link --rounds 2
```
