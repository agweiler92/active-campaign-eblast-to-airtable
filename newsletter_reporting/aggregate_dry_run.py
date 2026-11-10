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

# Default reporting period is the most recently completed Monday-Sunday week.
# For example, a run on Monday, Nov 9 uses Nov 2-Nov 8.
WINDOW_START = TODAY - timedelta(days=TODAY.weekday() + 7)
WINDOW_END = WINDOW_START + timedelta(days=6)
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


def campaign_message_contains_url(campaign_id, target_url, message_html_cache):
    """
    Return True when the advertiser's domain appears in the saved campaign HTML,
    False when it does not appear, or None when message presence cannot be verified.
    The check is deliberately conservative: a present domain with no click-report
    row remains a review item, never an assumed zero.
    """
    campaign_id = str(campaign_id)

    if campaign_id not in message_html_cache:
        message_html_cache[campaign_id] = None
        try:
            api_url = getattr(activecampaign, "API_URL", "")
            headers = getattr(activecampaign, "HEADERS", {})
            if not api_url:
                raise RuntimeError("ActiveCampaign API URL unavailable")

            campaign_message_response = requests.get(
                f"{api_url.rstrip('/')}/api/3/campaigns/{campaign_id}/campaignMessage",
                headers=headers,
                timeout=30,
            )
            campaign_message_response.raise_for_status()
            campaign_message_payload = campaign_message_response.json()
            campaign_message = (
                campaign_message_payload.get("campaignMessage", {})
                if isinstance(campaign_message_payload, dict) else {}
            )
            message_id = campaign_message.get("messageid") or campaign_message.get("message")
            if not message_id:
                raise RuntimeError("Campaign message ID unavailable")

            response = requests.get(
                f"{api_url.rstrip('/')}/api/3/messages/{message_id}",
                headers=headers,
                timeout=30,
            )
            response.raise_for_status()
            payload = response.json()
            message = payload.get("message", {}) if isinstance(payload, dict) else {}
            body = message.get("html") if isinstance(message, dict) else None

            if isinstance(body, str) and body.strip():
                message_html_cache[campaign_id] = body
        except Exception as exc:
            print(
                f"REVIEW: Could not inspect email HTML for campaign "
                f"{campaign_id}: {type(exc).__name__}"
            )

    body = message_html_cache.get(campaign_id)
    if not body:
        return None

    target_host = (urlparse(target_url).hostname or "").lower()
    if not target_host:
        return None

    target_hosts = {target_host}
    if target_host.startswith("www."):
        target_hosts.add(target_host[4:])
    else:
        target_hosts.add(f"www.{target_host}")

    candidate = html.unescape(body)
    for _ in range(7):
        lowered = candidate.lower()
        if any(host in lowered for host in target_hosts):
            return True

        decoded = unquote(html.unescape(candidate))
        if decoded == candidate:
            break
        candidate = decoded

    return False

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
        # Use ActiveCampaign uniqueopens (distinct contacts), never total opens.
        number(("unique_opens", "uniqueopens")),
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
    parser.add_argument(
        "--start-date",
        type=date.fromisoformat,
        help="Optional reporting-window start date (YYYY-MM-DD). Must be used with --end-date.",
    )
    parser.add_argument(
        "--end-date",
        type=date.fromisoformat,
        help="Optional reporting-window end date (YYYY-MM-DD). Must be used with --start-date.",
    )
    args = parser.parse_args()

    if args.yes and not args.write:
        parser.error("--yes requires --write")

    if (args.start_date is None) != (args.end_date is None):
        parser.error("--start-date and --end-date must be provided together")

    report_start = args.start_date or WINDOW_START
    report_end = args.end_date or WINDOW_END

    if report_start > report_end:
        parser.error("--start-date must be on or before --end-date")
    if report_end > TODAY:
        parser.error("--end-date cannot be in the future")

    print(f"Reporting window: {report_start} through {report_end}")
    print("Open metric: uniqueopens (distinct contacts)")
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

    # Initialize campaign results independently of whether insertions exist.
    campaigns = []
    missing_list_count = 0
    multiple_lists_count = 0

    for record in records:
        fields = record.get("fields", record)
        insertion_date = parse_date(fields.get("Date"))
        if not insertion_date or not (report_start <= insertion_date <= report_end):
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

    for campaign in activecampaign.get_all_campaigns():
        name = get_campaign_name(campaign)
        sent_date = get_campaign_date(campaign)

        if str(campaign.get("status", "")) != "5" or not sent_date:
            continue

        if not (report_start <= sent_date <= report_end):
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
    message_html_cache = {}
    for insertion in insertions:
        relevant = sorted(
            [
                campaign for campaign in campaigns
                if campaign["newsletter"] == insertion["newsletter"]
                and insertion["date"] <= campaign["date"] <= report_end
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

        confirmed_absent_campaigns = []

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
                    f"{campaign['name']} (ID {campaign['id']}) has "
                    f"{len(matches)} matching link-report rows"
                )
            else:
                included = campaign_message_contains_url(
                    campaign["id"],
                    insertion["url"],
                    message_html_cache,
                )

                if included is False:
                    campaign_clicks = 0
                    confirmed_absent_campaigns.append(
                        f"{campaign['name']} (ID {campaign['id']})"
                    )
                elif included is True:
                    campaign_clicks = "LINK_PRESENT_NO_REPORT_ROW"
                    missing_link_flags.append(
                        f"{campaign['name']} (ID {campaign['id']}): "
                        "URL appears in saved email HTML but has no click-report row"
                    )
                else:
                    campaign_clicks = "PRESENCE_UNVERIFIED"
                    missing_link_flags.append(
                        f"{campaign['name']} (ID {campaign['id']}): "
                        "could not verify whether URL was included in the email"
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
            "opens_metric_basis": "uniqueopens",
            "current_total_clicks": fields.get("Total Clicks", ""),
            "confirmed_clicks_from_report_rows": clicks_total,
            "confirmed_zero_click_campaigns": "; ".join(confirmed_absent_campaigns),
            "proposed_total_clicks": (
                "REVIEW_DUPLICATE_LINKS" if duplicate_flags
                else "REVIEW_MISSING_LINKS" if missing_link_flags
                else clicks_total
            ),
            "click_reporting_status": (
                "REVIEW_DUPLICATE_LINKS" if duplicate_flags
                else "REVIEW_MISSING_LINKS" if missing_link_flags
                else "COMPLETE"
            ),
            "missing_link_review": "; ".join(missing_link_flags),
            "click_sync_date_proposed": TODAY.isoformat(),
            "duplicate_link_review": "; ".join(duplicate_flags),
            "campaign_detail": " | ".join(details),
            "write_status": "DRY RUN ONLY - NOT WRITTEN",
        })

        # Review status is separated by metric group. Uncertain clicks do not block
    # otherwise valid subscriber/open updates, but click fields are held back.
    def is_number(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool)

    review_rows = []
    core_review_ids = set()
    click_review_ids = set()

    for row in output_rows:
        core_metrics_need_review = (
            not row.get("record_id")
            or int(row.get("campaigns_count") or 0) == 0
            or not is_number(row["proposed_subscribers"])
            or not is_number(row["proposed_total_opens"])
        )
        clicks_need_review = not is_number(row["proposed_total_clicks"])

        if core_metrics_need_review:
            core_review_ids.add(row["record_id"])
        if clicks_need_review:
            click_review_ids.add(row["record_id"])
        if core_metrics_need_review or clicks_need_review:
            review_rows.append(row)

    # Default behavior remains strictly read-only.
    if not args.write:
        for row in output_rows:
            row["write_status"] = "DRY RUN ONLY - NOT WRITTEN"

    else:
        writable_rows = [
            row for row in output_rows
            if row["record_id"] not in core_review_ids
        ]

        print(f"Rows eligible for subscriber/open updates: {len(writable_rows)}")
        print(f"Rows skipped for core metric review: {len(core_review_ids)}")
        print(f"Rows with clicks held for review: {len(click_review_ids)}")

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
                    record_id = row["record_id"]

                    if record_id in core_review_ids:
                        row["write_status"] = "SKIPPED - CORE METRICS REVIEW"
                        continue

                    fields_to_update = {
                        "Subscribers": row["proposed_subscribers"],
                        "Total Opens": row["proposed_total_opens"],
                    }

                    clicks_are_complete = record_id not in click_review_ids
                    if clicks_are_complete:
                        fields_to_update["Total Clicks"] = row["proposed_total_clicks"]
                        fields_to_update["Click Sync Date"] = row["click_sync_date_proposed"]

                    try:
                        airtable.update_record(record_id, fields_to_update)
                        row["write_status"] = (
                            "WRITTEN" if clicks_are_complete
                            else "WRITTEN - CLICKS HELD FOR REVIEW"
                        )

                    except Exception as exc:
                        row["write_status"] = (
                            f"WRITE FAILED - {type(exc).__name__}"
                        )
                        print(
                            f"ERROR: Update failed for record "
                            f"{record_id} ({type(exc).__name__})"
                        )

        else:
            for row in output_rows:
                row["write_status"] = "SKIPPED - NO ELIGIBLE CORE METRICS"
            print("No eligible subscriber/open rows to update.")

        # Write the CSV after processing so its status column reflects the outcome.
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    columns = list(output_rows[0].keys()) if output_rows else [
        "record_id", "advertiser", "site", "newsletter", "insertion_date",
        "url", "campaigns_count", "current_subscribers", "proposed_subscribers",
        "current_total_opens", "proposed_total_opens", "opens_metric_basis",
        "current_total_clicks", "confirmed_clicks_from_report_rows",
        "confirmed_zero_click_campaigns",
        "proposed_total_clicks", "click_reporting_status", "missing_link_review",
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
        str(row["write_status"]).startswith("WRITTEN")
        for row in output_rows
    )
    full_click_write_count = sum(
        row["write_status"] == "WRITTEN" for row in output_rows
    )
    clicks_held_count = sum(
        row["write_status"] == "WRITTEN - CLICKS HELD FOR REVIEW"
        for row in output_rows
    )
    failed_count = sum(
        str(row["write_status"]).startswith("WRITE FAILED")
        for row in output_rows
    )

    print(f"Airtable records successfully updated: {written_count}")
    print(f"Records with complete click totals written: {full_click_write_count}")
    print(f"Records with clicks held for review: {clicks_held_count}")
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
