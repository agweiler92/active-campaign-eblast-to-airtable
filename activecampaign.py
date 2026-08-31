import os
import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env")

API_URL = os.getenv("ACTIVECAMPAIGN_API_URL")
API_KEY = os.getenv("ACTIVECAMPAIGN_API_KEY")

if not API_URL:
    raise ValueError(
        "ACTIVECAMPAIGN_API_URL is not set in .env"
    )

if not API_KEY:
    raise ValueError(
        "ACTIVECAMPAIGN_API_KEY is not set in .env"
    )

HEADERS = {
    "Api-Token": API_KEY,
    "Content-Type": "application/json",
}


def get_all_campaigns():
    """Retrieve all campaigns from ActiveCampaign using pagination."""

    campaigns = []
    offset = 0
    limit = 100

    while True:
        url = f"{API_URL}/api/3/campaigns"

        response = requests.get(
            url,
            headers=HEADERS,
            params={
                "limit": limit,
                "offset": offset,
            },
            timeout=30,
        )

        response.raise_for_status()

        data = response.json()
        batch = data.get("campaigns", [])

        campaigns.extend(batch)

        print(
            f"Retrieved {len(batch)} campaigns "
            f"(offset {offset})"
        )

        if len(batch) < limit:
            break

        offset += limit

    return campaigns


def get_campaign(campaign_id):
    """Retrieve the complete campaign object."""

    url = f"{API_URL}/api/3/campaigns/{campaign_id}"

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=30,
    )

    response.raise_for_status()

    return response.json()


def get_campaign_lists(campaign_id):
    """Retrieve all lists associated with a campaign."""

    url = f"{API_URL}/api/3/campaigns/{campaign_id}/campaignLists"

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=30,
    )

    response.raise_for_status()

    return response.json()


def get_list(list_id):
    """Retrieve the complete ActiveCampaign list object."""

    url = f"{API_URL}/api/3/lists/{list_id}"

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=30,
    )

    response.raise_for_status()

    return response.json()


def is_dedicated_eblast(campaign):
    """Return True if the campaign follows the Dedicated Eblast naming convention."""

    name = campaign.get("name", "")

    return name.startswith("Dedicated Eblast_")


def extract_advertiser(campaign):
    """
    Extract advertiser from:

    Dedicated Eblast_ADVERTISER_DATE
    """

    name = campaign.get("name", "")

    if not is_dedicated_eblast(campaign):
        return None

    parts = name.split("_", 2)

    if len(parts) < 3:
        return None

    return parts[1].strip()


def get_send_date(campaign):
    """Return the ActiveCampaign send date as YYYY-MM-DD."""

    sdate = campaign.get("sdate")

    if not sdate:
        return None

    return sdate[:10]


def get_campaign_metrics(campaign):
    """Return the campaign performance metrics."""

    return {
        "subscribers": int(campaign.get("send_amt") or 0),
        "opens": int(campaign.get("opens") or 0),
        "clicks": int(campaign.get("linkclicks") or 0),
        "unique_opens": int(campaign.get("uniqueopens") or 0),
        "unique_clicks": int(
            campaign.get("uniquelinkclicks") or 0
        ),
        "unsubscribes": int(
            campaign.get("unsubscribes") or 0
        ),
        "hard_bounces": int(
            campaign.get("hardbounces") or 0
        ),
        "soft_bounces": int(
            campaign.get("softbounces") or 0
        ),
    }


if __name__ == "__main__":

    print("=" * 80)
    print("ACTIVE CAMPAIGN DISCOVERY")
    print("=" * 80)
    print()

    # ------------------------------------------------------------------
    # Retrieve all campaigns
    # ------------------------------------------------------------------

    campaigns = get_all_campaigns()

    print()
    print(f"Total campaigns retrieved: {len(campaigns)}")
    print()

    # ------------------------------------------------------------------
    # Find Dedicated Eblast campaigns
    # ------------------------------------------------------------------

    dedicated_campaigns = [
        campaign
        for campaign in campaigns
        if is_dedicated_eblast(campaign)
    ]

    print(
        f"Dedicated Eblast campaigns found: "
        f"{len(dedicated_campaigns)}"
    )

    print()

    # ------------------------------------------------------------------
    # Process every Dedicated Eblast
    # ------------------------------------------------------------------

    for campaign in dedicated_campaigns:

        campaign_id = campaign.get("id")
        campaign_name = campaign.get("name")
        advertiser = extract_advertiser(campaign)
        send_date = get_send_date(campaign)
        metrics = get_campaign_metrics(campaign)

        print()
        print("=" * 80)
        print("CAMPAIGN")
        print("=" * 80)

        print(f"Campaign ID:       {campaign_id}")
        print(f"Campaign Name:     {campaign_name}")
        print(f"Advertiser:        {advertiser}")
        print(f"Send Date:         {send_date}")
        print(f"Status:             {campaign.get('status')}")
        print(f"Status String:      {campaign.get('statusString')}")
        print(f"Type:               {campaign.get('type')}")
        print()

        print("METRICS")
        print("-" * 80)

        print(f"Subscribers:        {metrics['subscribers']}")
        print(f"Opens:              {metrics['opens']}")
        print(f"Unique Opens:       {metrics['unique_opens']}")
        print(f"Clicks:             {metrics['clicks']}")
        print(f"Unique Clicks:      {metrics['unique_clicks']}")
        print(f"Unsubscribes:       {metrics['unsubscribes']}")
        print(f"Hard Bounces:       {metrics['hard_bounces']}")
        print(f"Soft Bounces:       {metrics['soft_bounces']}")
        print()

        # ------------------------------------------------------------------
        # Campaign lists
        # ------------------------------------------------------------------

        print("CAMPAIGN LIST API RESPONSE")
        print("-" * 80)

        try:
            campaign_lists = get_campaign_lists(campaign_id)

            print(campaign_lists)

        except requests.exceptions.HTTPError as e:

            print(
                f"ERROR retrieving campaign lists: {e}"
            )

            campaign_lists = {}

        print()

        # ------------------------------------------------------------------
        # Retrieve the actual list details
        # ------------------------------------------------------------------

        list_relationships = campaign_lists.get(
            "campaignLists",
            []
        )

        if not list_relationships:

            print("No campaign list relationships found.")

        else:

            print("LIST DETAILS")
            print("-" * 80)

            for relationship in list_relationships:

                print()
                print("Campaign/List Relationship:")
                print(relationship)

                list_id = relationship.get("list")

                if not list_id:
                    continue

                try:

                    list_data = get_list(list_id)

                    print()
                    print("FULL LIST API RESPONSE:")
                    print(list_data)

                    list_object = list_data.get(
                        "list",
                        {}
                    )

                    print()
                    print(
                        f"LIST NAME: "
                        f"{list_object.get('name')}"
                    )

                except requests.exceptions.HTTPError as e:

                    print(
                        f"ERROR retrieving list "
                        f"{list_id}: {e}"
                    )

        print()

        # ------------------------------------------------------------------
        # Full campaign object
        # ------------------------------------------------------------------

        print("FULL CAMPAIGN OBJECT")
        print("-" * 80)

        try:

            full_campaign = get_campaign(campaign_id)

            print(full_campaign)

        except requests.exceptions.HTTPError as e:

            print(
                f"ERROR retrieving full campaign: {e}"
            )

        print()
        print("=" * 80)
        print("END CAMPAIGN")
        print("=" * 80)
        print()