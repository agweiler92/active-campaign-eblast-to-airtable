"""Read-only newsletter metrics aggregation dry run."""
from __future__ import annotations

import csv
import html
import re
import sys
import argparse
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, unquote

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import activecampaign
import airtable

TODAY = date.today()
WINDOW_START = TODAY - timedelta(days=6)
OUTPUT = ROOT / "newsletter_reporting" / "newsletter_aggregation_dry_run.csv"
EXCLUDED_ADVERTISER = "university of san francisco"

ALIASES = {
    "weekly oakland news": "Weekly Oakland News",
    "daily oakalnd news": "Daily Oakland News",
    "daily oaklandside": "Daily Oakland News",
    "this week in berkeley": "This Week in Berkeley",
    "daily briefing": "Daily Briefing",
    "richmondside report": "Richmondside Report",
    "nosh newsletter": "Nosh Newsletter",
    "the scene": "The Scene",
}
IGNORED_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term",
    "utm_content", "gclid", "fbclid", "mc_cid", "mc_eid",
}


def canonical_newsletter(value):
    value = (value or "").strip().lower()
    return ALIASES.get(value, value.title())


def get_campaign_name(campaign):
    return str(campaign.get("name") or campaign.get("campaign") or "").strip()



def get_campaign_date(campaign):
    """Return the actual campaign send date from ActiveCampaign's sdate."""
    sdate = campaign.get("sdate")

    if not sdate or sdate.startswith("0000-00-00"):
        return None

    try:
        return datetime.fromisoformat(
            sdate.replace("Z", "+00:00")
        ).date()
    except (ValueError, TypeError):
        return None


def decode_newspack_url(url):
    current = html.unescape((url or "").strip())
    for _ in range(5):
        params = parse_qs(urlparse(current).query, keep_blank_values=True)
        if params.get("np_newsletters_click") and params.get("url"):
            destination = html.unescape(params["url"][0]).replace("&amp;", "&")
            if destination and destination != current:
                current = destination
                continue
        decoded = html.unescape(unquote(current)).replace("&amp;", "&")
        if decoded != current and decoded.startswith(("http://", "https://")):
            current = decoded
            continue
        break
    return current.replace("&amp;", "&")


def normalize_url(url):
    parsed = urlparse(decode_newspack_url(url))
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = unquote(parsed.path or "/")
    query = []
    for key, values in parse_qs(parsed.query, keep_blank_values=True).items():
        if key.lower() not in IGNORED_PARAMS:
            query.extend((key, value) for value in values)
    query.sort()
    return host, path, urlencode(query, doseq=True)


def get_link_rows(campaign_id):
    api_url = getattr(activecampaign, "API_URL", "")
    headers = getattr(activecampaign, "HEADERS", {})
    if not api_url:
        raise RuntimeError("API_URL was not found in activecampaign.py")

    response = requests.get(
        f"{api_url.rstrip('/')}/admin/api.php",
        headers=headers,
        params={
            "api_action": "campaign_report_link_list",
            "api_output": "json",
            "campaignid": campaign_id,
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()

    if isinstance(payload, dict):
        rows = [
            value for key, value in payload.items()
            if str(key).isdigit() and isinstance(value, dict)
        ]
    elif isinstance(payload, list):
        rows = [row for row in payload if isinstance(row, dict)]
    else:
        rows = []

    result = []
    for row in rows:
        url = str(row.get("link") or "").strip()
        if not url:
            continue
        try:
            clicks = int(float(row.get("a_total") or 0))
        except (TypeError, ValueError):
            clicks = 0
        # Never retain or print subscriber-level "info" data.
        result.append({
            "normalized_url": normalize_url(url),
            "clicks": clicks,
        })
    return result

def get_campaign_detail(campaign_id):
    """Retrieve detailed campaign information, including associated lists."""
    api_url = getattr(activecampaign, "API_URL", "")
    headers = getattr(activecampaign, "HEADERS", {})

    if not api_url:
        raise RuntimeError("API_URL was not found in activecampaign.py")

    response = requests.get(
        f"{api_url.rstrip('/')}/admin/api.php",
        headers=headers,
        params={
            "api_action": "campaign_list",
            "api_output": "json",
            "ids": str(campaign_id),
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()

    if isinstance(payload, dict):
        if payload.get("id") or payload.get("campaignid"):
            return payload

        for key, value in payload.items():
            if str(key).isdigit() and isinstance(value, dict):
                return value

        if isinstance(payload.get("campaign"), dict):
            return payload["campaign"]

    elif isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                return item

    return None

def get_campaign_metrics(campaign):
    metrics = activecampaign.get_campaign_metrics(campaign)
    if not isinstance(metrics, dict):
        return None, None

    def number(keys):
        for key in keys:
            value = metrics.get(key)
            if value is not None and value != "":
                try:
                    return int(float(value))
                except (TypeError, ValueError):
                    pass
        return None

    return (
        number(("subscribers", "send_amt", "sent", "sent_to")),
        number(("total_opens", "opens", "open_amt")),
    )


def as_text(value):
    if isinstance(value, list):
        return ", ".join(
            str(item.get("name", item)) if isinstance(item, dict) else str(item)
            for item in value
        )
    return "" if value is None else str(value).strip()


def parse_date(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
            try:
                return datetime.strptime(str(value), fmt).date()
            except ValueError:
                pass
    return None


def main():
    parser = argparse.ArgumentParser(
        description="Aggregate newsletter metrics from ActiveCampaign."
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Enable Airtable updates.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip interactive confirmation; use only in trusted automation.",
    )
    args = parser.parse_args()

    if args.yes and not args.write:
        parser.error("--yes requires --write")

    print(f"Reporting window: {WINDOW_START} through {TODAY}")
    if not args.write:
        write_status = "DISABLED"
    elif args.yes:
        write_status = "ENABLED — non-interactive"
    else:
        write_status = "ENABLED — confirmation required"

    print(f"Airtable writes: {write_status}")

    records = airtable.get_all_records()
    if not isinstance(records, list):
        raise RuntimeError("airtable.get_all_records() did not return a list")

    insertions = []
    missing_newsletter = []
    excluded_count = 0
    for record in records:
        fields = record.get("fields", record)
        insertion_date = parse_date(fields.get("Date"))
        if not insertion_date or not (WINDOW_START <= insertion_date <= TODAY):
            continue

        advertiser = as_text(fields.get("Advertiser"))
        if EXCLUDED_ADVERTISER in advertiser.lower():
            excluded_count += 1
            print(f"Excluded known URL exception: {advertiser}")
            continue

        newsletter = canonical_newsletter(as_text(fields.get("Newsletter")))
        if not newsletter:
            missing_newsletter.append({
                "record_id": record.get("id", ""),
                "advertiser": advertiser,
                "date": insertion_date.isoformat(),
                "url": as_text(fields.get("URL")),
            })
            continue

        url = as_text(fields.get("URL"))
        insertions.append({
            "record_id": record.get("id", ""),
            "advertiser": advertiser,
            "site": as_text(fields.get("Site")),
            "date": insertion_date,
            "newsletter": newsletter,
            "url": url,
            "normalized_url": normalize_url(url),
            "fields": fields,
        })

        campaigns = []
    missing_list_count = 0
    multiple_lists_count = 0

    for campaign in activecampaign.get_all_campaigns():
        name = get_campaign_name(campaign)
        sent_date = get_campaign_date(campaign)

        if str(campaign.get("status", "")) != "5" or not sent_date:
            continue

        if not (WINDOW_START <= sent_date <= TODAY):
            continue

        cid = str(campaign.get("id") or campaign.get("campaignid") or "")
        if not cid:
            continue

        try:
            detail = get_campaign_detail(cid)
        except Exception as exc:
            missing_list_count += 1
            print(
                f"REVIEW: Could not retrieve details for campaign {cid}: "
                f"{type(exc).__name__}"
            )
            continue

        if not detail:
            missing_list_count += 1
            print(f"REVIEW: No campaign detail returned for {cid}")
            continue

        raw_lists = detail.get("lists") or []

        if isinstance(raw_lists, dict):
            raw_lists = list(raw_lists.values())

        if not isinstance(raw_lists, list):
            raw_lists = []

        list_names = list(dict.fromkeys(
            str(item.get("name") or "").strip()
            for item in raw_lists
            if isinstance(item, dict)
            and str(item.get("name") or "").strip()
        ))

        if len(list_names) == 0:
            missing_list_count += 1
            print(f"REVIEW: Campaign {cid} has no list association")
            continue

        if len(list_names) > 1:
            multiple_lists_count += 1
            print(
                f"REVIEW: Campaign {cid} has multiple lists: "
                f"{', '.join(list_names)}"
            )
            continue

        newsletter = canonical_newsletter(list_names[0])
        subscribers, opens = get_campaign_metrics(campaign)

        campaigns.append({
            "id": cid,
            "name": name,
            "date": sent_date,
            "newsletter": newsletter,
            "subscribers": subscribers,
            "opens": opens,
            "links": get_link_rows(cid),
        })

    print(f"Eligible sent campaigns in window: {len(campaigns)}")
    print(f"Campaigns skipped for missing list association: {missing_list_count}")
    print(f"Campaigns skipped for multiple list associations: {multiple_lists_count}")

    output_rows = []
    for insertion in insertions:
        relevant = sorted(
            [
                campaign for campaign in campaigns
                if campaign["newsletter"] == insertion["newsletter"]
                and insertion["date"] <= campaign["date"] <= TODAY
            ],
            key=lambda campaign: (campaign["date"], campaign["id"]),
        )

        subscriber_total = 0
        opens_total = 0
        subscriber_missing = False
        opens_missing = False
        clicks_total = 0
        duplicate_flags = []
        missing_link_flags = []
        details = []

        for campaign in relevant:
            if campaign["subscribers"] is None:
                subscriber_missing = True
            else:
                subscriber_total += campaign["subscribers"]

            if campaign["opens"] is None:
                opens_missing = True
            else:
                opens_total += campaign["opens"]

            matches = [
                link for link in campaign["links"]
                if link["normalized_url"] == insertion["normalized_url"]
            ]

            if len(matches) == 1:
                campaign_clicks = matches[0]["clicks"]
                clicks_total += campaign_clicks
            elif len(matches) > 1:
                campaign_clicks = "DUPLICATE_REVIEW"
                duplicate_flags.append(
                    f"{campaign['name']} has {len(matches)} matching link rows"
                )
            else:
                campaign_clicks = "NO_MATCH"
                missing_link_flags.append(
                    f"{campaign['name']} (ID {campaign['id']})"
                )

            details.append(
                f"{campaign['date']} {campaign['name']} ID={campaign['id']}; "
                f"subs={campaign['subscribers']}; opens={campaign['opens']}; "
                f"advertiser_clicks={campaign_clicks}"
            )

        fields = insertion["fields"]
        output_rows.append({
            "record_id": insertion["record_id"],
            "advertiser": insertion["advertiser"],
            "site": insertion["site"],
            "newsletter": insertion["newsletter"],
            "insertion_date": insertion["date"].isoformat(),
            "url": insertion["url"],
            "campaigns_count": len(relevant),
            "current_subscribers": fields.get("Subscribers", ""),
            "proposed_subscribers": (
                "REVIEW_MISSING_METRICS" if subscriber_missing else subscriber_total
            ),
            "current_total_opens": fields.get("Total Opens", ""),
            "proposed_total_opens": (
                "REVIEW_MISSING_METRICS" if opens_missing else opens_total
            ),
            "current_total_clicks": fields.get("Total Clicks", ""),
                          "proposed_total_clicks": (
                  "REVIEW_DUPLICATE_LINKS" if duplicate_flags
                  else "REVIEW_MISSING_LINKS" if missing_link_flags
                  else clicks_total
              ),
              "missing_link_review": "; ".join(missing_link_flags),
            "click_sync_date_proposed": TODAY.isoformat(),
            "duplicate_link_review": "; ".join(duplicate_flags),
            "campaign_detail": " | ".join(details),
            "write_status": "DRY RUN ONLY - NOT WRITTEN",
        })

        # Identify records that must not be written.
    review_rows = []

    for row in output_rows:
        proposed_metrics = (
            row["proposed_subscribers"],
            row["proposed_total_opens"],
            row["proposed_total_clicks"],
        )

        has_review_marker = any(
            "REVIEW" in str(value)
            for value in proposed_metrics
        )
        has_link_flags = bool(
            row.get("missing_link_review")
            or row.get("duplicate_link_review")
        )
        has_no_campaigns = int(row.get("campaigns_count") or 0) == 0
        has_missing_record_id = not row.get("record_id")
        has_non_numeric_metrics = not all(
            isinstance(value, (int, float)) and not isinstance(value, bool)
            for value in proposed_metrics
        )

        if (
            has_review_marker
            or has_link_flags
            or has_no_campaigns
            or has_missing_record_id
            or has_non_numeric_metrics
        ):
            review_rows.append(row)

    review_ids = {row["record_id"] for row in review_rows}

    # Default behavior remains strictly read-only.
    if not args.write:
        for row in output_rows:
            row["write_status"] = "DRY RUN ONLY - NOT WRITTEN"

    else:
        writable_rows = [
            row for row in output_rows
            if row["record_id"] not in review_ids
        ]

        print(f"Rows eligible for Airtable update: {len(writable_rows)}")
        print(f"Rows skipped for review: {len(review_rows)}")

        if writable_rows:
            if args.yes:
                confirmation = "WRITE"
            else:
                try:
                    confirmation = input(
                        'Type WRITE to update the approved Airtable fields: '
                    ).strip()
                except EOFError:
                    confirmation = ""

            if confirmation != "WRITE":
                for row in output_rows:
                    row["write_status"] = "WRITE CANCELLED - NOT WRITTEN"
                print("Confirmation not received. No Airtable updates were made.")

            else:
                for row in output_rows:
                    if row["record_id"] in review_ids:
                        row["write_status"] = "SKIPPED - REVIEW"
                        continue

                    fields_to_update = {
                        "Subscribers": row["proposed_subscribers"],
                        "Total Opens": row["proposed_total_opens"],
                        "Total Clicks": row["proposed_total_clicks"],
                        "Click Sync Date": row["click_sync_date_proposed"],
                    }

                    try:
                        airtable.update_record(
                            row["record_id"],
                            fields_to_update,
                        )
                        row["write_status"] = "WRITTEN"

                    except Exception as exc:
                        row["write_status"] = (
                            f"WRITE FAILED - {type(exc).__name__}"
                        )
                        print(
                            f"ERROR: Update failed for record "
                            f"{row['record_id']} ({type(exc).__name__})"
                        )

        else:
            for row in output_rows:
                row["write_status"] = "SKIPPED - NO ELIGIBLE ROWS"
            print("No eligible rows to update.")

    # Write the CSV after processing so its status column reflects the outcome.
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    columns = list(output_rows[0].keys()) if output_rows else [
        "record_id", "advertiser", "site", "newsletter", "insertion_date",
        "url", "campaigns_count", "current_subscribers", "proposed_subscribers",
        "current_total_opens", "proposed_total_opens", "current_total_clicks",
        "proposed_total_clicks", "missing_link_review",
        "click_sync_date_proposed", "duplicate_link_review",
        "campaign_detail", "write_status",
    ]

    with OUTPUT.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(output_rows)

    print(f"Eligible insertions (excluding USF): {len(insertions)}")
    print(f"Excluded USF exceptions: {excluded_count}")
    print(f"Eligible sent campaigns in window: {len(campaigns)}")
    print(f"CSV created: {OUTPUT}")
    print(f"Rows requiring review: {len(review_rows)} of {len(output_rows)}")

    written_count = sum(
        row["write_status"] == "WRITTEN" for row in output_rows
    )
    failed_count = sum(
        str(row["write_status"]).startswith("WRITE FAILED")
        for row in output_rows
    )

    print(f"Airtable records successfully updated: {written_count}")
    print(f"Airtable update failures: {failed_count}")
    for row in review_rows:
        print(
            f"  {row['advertiser']} | {row['newsletter']} | "
            f"subs={row['proposed_subscribers']} | "
            f"opens={row['proposed_total_opens']} | "
            f"clicks={row['proposed_total_clicks']}"
        )


if __name__ == "__main__":
    main()
