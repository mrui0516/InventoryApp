from datetime import datetime

from django.core.management.base import BaseCommand, CommandError

from stock.services import rebuild_all_daily_summaries, recalc_summary_for_date


class Command(BaseCommand):
    help = "Rebuild DailySalesSummary using FIFO replay."

    def add_arguments(self, parser):
        parser.add_argument(
            "--date",
            type=str,
            help="Specific date (YYYY-MM-DD) to rebuild summary for.",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="Rebuild summaries for all sale dates.",
        )

    def handle(self, *args, **options):
        date_str = options.get("date")
        rebuild_all = options.get("all")

        if rebuild_all and date_str:
            raise CommandError("Use either --date=YYYY-MM-DD or --all, not both.")
        if not rebuild_all and not date_str:
            raise CommandError("Please provide --date=YYYY-MM-DD or use --all.")

        if rebuild_all:
            rebuilt = rebuild_all_daily_summaries()
            self.stdout.write(self.style.SUCCESS(f"Rebuilt {rebuilt} daily summaries."))
            return

        try:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError as exc:
            raise CommandError("Invalid date format. Use YYYY-MM-DD") from exc

        self.stdout.write(self.style.WARNING(f"Rebuilding DailySalesSummary for {target_date} (FIFO simulation)..."))
        summary = recalc_summary_for_date(target_date)

        if summary is None:
            self.stdout.write(self.style.WARNING(f"No sales found for {target_date}. Summary deleted if it existed."))
            return

        cost = summary.total_sales - summary.total_profit
        self.stdout.write(
            self.style.SUCCESS(
                f"{target_date}: Sales={summary.total_sales:.2f}, Cost={cost:.2f}, Profit={summary.total_profit:.2f}, Items={summary.total_items_sold}"
            )
        )
