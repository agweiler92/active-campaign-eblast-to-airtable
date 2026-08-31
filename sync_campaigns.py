from activecampaign import (
    get_all_campaigns,
    is_dedicated_eblast,
    extract_advertiser,
    get_send_date,
    get_campaign_metrics,
    get_campaign_lists,
)

from airtable import (
    get_all_records,
    update_record,
)


SITE_MAPPING = {
    "Berkeleyside Sponsored Content Emails": "Berkeleyside",
    "Oaklandside Sponsored Content Emails": "The Oaklandside",
    "Richmondside Sponsored Content Emails": "Richmondside",
    "Nosh Sponsored Content Emails": "Nosh",
}


def get_list_names(campaign_id):
    """Get the names of lists associated with a campaign."""

    try:
        data = get_campaign_lists(campaign_id)

        relationships = data.get("campaignLists", [])

        list_names = []

        for relationship in relationships:

            name = relationship.get("name")

            if name:
                list_names.append(name)

        return list_names

    except Exception as e:

        print(
            f"Could not retrieve lists for campaign "
            f"{campaign_id}: {e}"
        )

        return []


def find_airtable_match(
    records,
    advertiser,
    send_date,
    list_names,
):
    """
    Find an Airtable record matching:

    Advertiser
    Date
    Site

    Placement must contain "Dedicated Eblast".
    """

    site = None

    for list_name in list_names:

        if list_name in SITE_MAPPING:

            site = SITE_MAPPING[list_name]

            break

    if not site:

        return [], None

    matches = []

    for record in records:

        fields = record.get("fields", {})

        record_advertiser = fields.get("Advertiser")
        record_date = fields.get("Date")
        record_site = fields.get("Site")
        placement = fields.get("Placement", [])

        if isinstance(record_site, str):
            record_site = [record_site]

        if isinstance(placement, str):
            placement = [placement]

        if record_site is None:
            record_site = []

        if placement is None:
            placement = []

        if (
            record_advertiser == advertiser
            and record_date == send_date
            and site in record_site
            and "Dedicated Eblast" in placement
        ):
            matches.append(record)

    return matches, site


if __name__ == "__main__":

    print("=" * 80)
    print("ACTIVE CAMPAIGN → AIRTABLE SYNC")
    print("=" * 80)
    print()

    campaigns = get_all_campaigns()

    airtable_records = get_all_records()

    print(
        f"ActiveCampaign campaigns: "
        f"{len(campaigns)}"
    )

    print(
        f"Airtable records:         "
        f"{len(airtable_records)}"
    )

    dedicated_campaigns = [
        campaign
        for campaign in campaigns
        if is_dedicated_eblast(campaign)
    ]

    print(
        f"Dedicated Eblast campaigns: "
        f"{len(dedicated_campaigns)}"
    )

    print()

    updated = 0
    unmatched = 0
    missing_dates = 0
    unknown_sites = 0
    multiple_matches = 0
    errors = 0

    for campaign in dedicated_campaigns:

        campaign_id = campaign.get("id")
        campaign_name = campaign.get("name")

        advertiser = extract_advertiser(campaign)

        send_date = get_send_date(campaign)

        metrics = get_campaign_metrics(campaign)

        list_names = get_list_names(campaign_id)

        print()
        print("=" * 80)
        print("CAMPAIGN")
        print("-" * 80)

        print(f"ID:           {campaign_id}")
        print(f"Name:         {campaign_name}")
        print(f"Advertiser:   {advertiser}")
        print(f"Send Date:    {send_date}")
        print(f"Lists:        {list_names}")

        # -----------------------------------------------------------
        # Skip campaigns without a send date
        # -----------------------------------------------------------

        if not send_date:

            print()
            print("⚠️  NO SEND DATE — SKIPPING")

            missing_dates += 1

            continue

        # -----------------------------------------------------------
        # Find Airtable match
        # -----------------------------------------------------------

        matches, site = find_airtable_match(
            airtable_records,
            advertiser,
            send_date,
            list_names,
        )

        print(
            f"Airtable Site: {site}"
        )

        # -----------------------------------------------------------
        # Unknown site
        # -----------------------------------------------------------

        if not site:

            print()
            print(
                "⚠️  UNKNOWN ACTIVE CAMPAIGN LIST — SKIPPING"
            )

            unknown_sites += 1

            continue

        # -----------------------------------------------------------
        # No match
        # -----------------------------------------------------------

        if not matches:

            print()
            print("❌ NO AIRTABLE MATCH — NOTHING UPDATED")

            unmatched += 1

            continue

        # -----------------------------------------------------------
        # Multiple matches
        # -----------------------------------------------------------

        if len(matches) > 1:

            print()
            print(
                f"⚠️  MULTIPLE AIRTABLE MATCHES: "
                f"{len(matches)}"
            )

            print(
                "⚠️  NOTHING UPDATED FOR THIS CAMPAIGN"
            )

            multiple_matches += 1

            continue

        # -----------------------------------------------------------
        # Exactly one match
        # -----------------------------------------------------------

        record = matches[0]

        record_id = record.get("id")

        print()
        print("✅ AIRTABLE MATCH FOUND")
        print("-" * 80)

        print(
            f"Record ID:    {record_id}"
        )

        print(
            f"Advertiser:   {advertiser}"
        )

        print(
            f"Site:         {site}"
        )

        print(
            f"Date:         {send_date}"
        )

        print()
        print("UPDATING AIRTABLE:")
        print(
            f"Subscribers:  {metrics['subscribers']}"
        )

        print(
            f"Total Opens:  {metrics['opens']}"
        )

        print(
            f"Total Clicks: {metrics['clicks']}"
        )

        # -----------------------------------------------------------
        # Fields to update
        # -----------------------------------------------------------

        update_fields = {
            "Subscribers": metrics["subscribers"],
            "Total Opens": metrics["opens"],
            "Total Clicks": metrics["clicks"],
        }

        # -----------------------------------------------------------
        # Perform Airtable update
        # -----------------------------------------------------------

        try:

            update_record(
                record_id,
                update_fields,
            )

            print()
            print("✅ AIRTABLE UPDATED SUCCESSFULLY")

            updated += 1

        except Exception as e:

            print()
            print("❌ AIRTABLE UPDATE FAILED")

            print(
                f"Error: {e}"
            )

            errors += 1

    # ---------------------------------------------------------------
    # Summary
    # ---------------------------------------------------------------

    print()
    print()
    print("=" * 80)
    print("SYNC SUMMARY")
    print("=" * 80)

    print(
        f"Dedicated Eblasts:      "
        f"{len(dedicated_campaigns)}"
    )

    print(
        f"Records updated:        "
        f"{updated}"
    )

    print(
        f"Unmatched:              "
        f"{unmatched}"
    )

    print(
        f"Missing send dates:     "
        f"{missing_dates}"
    )

    print(
        f"Unknown sites:          "
        f"{unknown_sites}"
    )

    print(
        f"Multiple matches:       "
        f"{multiple_matches}"
    )

    print(
        f"Update errors:          "
        f"{errors}"
    )

    print()
    print("=" * 80)