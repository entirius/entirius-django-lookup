# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Precision/recall/F1 over a hand-labelled CSV — the calibration layer (test-strategy §4).

Never fails the build: a malformed row, an unknown kind, a bad label or a stale ref is skipped and
counted by reason, not raised — and `evaluate()` itself is guarded here too, so a defect in the
harness degrades the printed numbers, never the exit code. The numbers are the deliverable, not a
pass/fail gate — record them, discuss, then maybe retune `scoring.WEIGHTS` or `LOOKUP_THRESHOLDS`.
"""

from django.core.management.base import BaseCommand

from django_lookup.services import eval_service

DEFAULT_THRESHOLDS = [45, 75]


class Command(BaseCommand):
    help = "Run lookup check() over a labelled pairs CSV and print precision/recall/F1 per threshold."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--pairs", required=True, help="CSV: query_kind,query_ref,candidate_kind,candidate_ref,label,why"
        )
        parser.add_argument("--thresholds", default="45,75", help="Comma-separated score cutoffs to report")
        parser.add_argument(
            "--image-only",
            action="store_true",
            help="Report only recall@20 (image blocking) from each query's own embedded Fingerprint row",
        )
        parser.add_argument(
            "--log-decisions",
            action="store_true",
            help="Leave one DedupDecision row per candidate behind (source=lookup_eval); off by default "
            "because a full pairs file would otherwise flood the audit log",
        )

    def handle(self, *args, **options) -> None:
        try:
            pairs = eval_service.load_pairs(options["pairs"])
        except OSError as exc:
            self.stdout.write(self.style.ERROR(f"cannot read --pairs: {exc}"))
            return
        try:
            report = eval_service.evaluate(
                pairs,
                _parse_thresholds(options["thresholds"]),
                options["image_only"],
                log_decisions=options["log_decisions"],
            )
        except Exception as exc:  # the numbers must never cost the caller a traceback / non-zero exit
            self.stdout.write(self.style.ERROR(f"evaluate() failed: {exc}"))
            return
        self._print(report)

    def _print(self, report: eval_service.EvalReport) -> None:
        self.stdout.write(f"pairs: {report.total} (skipped {report.skipped}, not retrieved {report.not_retrieved})")
        self._print_skips(report.skip_reasons)
        if report.thresholds:
            self._print_thresholds(report.thresholds)
            self._print_thresholds(report.thresholds_with_variant)
            self._print_confusion(report.confusion)
            self._print_decision_view(report.confusion)
        self.stdout.write("")
        self.stdout.write(f"recall@50 (name blocking): {_fmt(report.recall_at_50_name)}")
        self.stdout.write(f"recall@20 (image blocking): {_fmt(report.recall_at_20_image)}")

    def _print_skips(self, reasons: dict[str, int]) -> None:
        if not reasons:
            return
        self.stdout.write(
            "skipped by reason: " + ", ".join(f"{reason}={count}" for reason, count in sorted(reasons.items()))
        )

    def _print_thresholds(self, thresholds: list[eval_service.ThresholdReport]) -> None:
        self.stdout.write("")
        self.stdout.write(f"positives: {thresholds[0].positives}")
        self.stdout.write("threshold  precision  recall     f1        tp   fp   fn   tn")
        for t in thresholds:
            self.stdout.write(
                f"{t.threshold:>9}  {t.precision:>9.2f}  {t.recall:>7.2f}  {t.f1:>7.2f}  "
                f"{t.tp:>3}  {t.fp:>3}  {t.fn:>3}  {t.tn:>3}"
            )

    def _print_decision_view(self, confusion: dict[tuple[str, str], int]) -> None:
        """What the product actually does, as opposed to what the raw score does.

        The threshold sweep above measures the SCORE; `decide()` also applies the identifier override and
        the capping flags, so the two views differ on purpose — read this one to answer "how many true
        pairs would the operator never have to look at?".
        """
        matched = confusion.get(("match", "match"), 0)
        reviewed = confusion.get(("match", "review"), 0)
        missed = confusion.get(("match", "no_match"), 0)
        total = matched + reviewed + missed
        if not total:
            return
        wrong = sum(count for (label, decision), count in confusion.items() if label == "no" and decision == "match")
        self.stdout.write("")
        self.stdout.write(
            f"decision view (label=match): auto-linked {matched}/{total} = {matched / total:.2f}, "
            f"to review {reviewed}, missed {missed}; wrongly auto-linked (label=no): {wrong}"
        )

    def _print_confusion(self, confusion: dict[tuple[str, str], int]) -> None:
        self.stdout.write("")
        self.stdout.write("confusion (label -> engine decision):")
        for (label, decision), count in sorted(confusion.items()):
            self.stdout.write(f"  {label:8} -> {decision:8} : {count}")


def _parse_thresholds(raw: str) -> list[int]:
    try:
        parsed = [int(part) for part in raw.split(",") if part.strip()]
    except ValueError:
        return DEFAULT_THRESHOLDS
    return parsed or DEFAULT_THRESHOLDS


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"
