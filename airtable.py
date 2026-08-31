import os
from dotenv import load_dotenv
from pyairtable import Api

load_dotenv()

AIRTABLE_TOKEN = os.getenv("AIRTABLE_TOKEN")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")
AIRTABLE_TABLE_NAME = os.getenv("AIRTABLE_TABLE_NAME")

if not AIRTABLE_TOKEN:
    raise ValueError("AIRTABLE_TOKEN is not set")

if not AIRTABLE_BASE_ID:
    raise ValueError("AIRTABLE_BASE_ID is not set")

if not AIRTABLE_TABLE_NAME:
    raise ValueError("AIRTABLE_TABLE_NAME is not set")


api = Api(AIRTABLE_TOKEN)
table = api.table(
    AIRTABLE_BASE_ID,
    AIRTABLE_TABLE_NAME
)


def get_all_records():
    """Retrieve all Airtable records."""

    return table.all()


def update_record(record_id, fields):
    """
    Update an existing Airtable record.
    """

    return table.update(
        record_id,
        fields
    )


def find_matching_record(advertiser, date):
    """
    Find an Airtable record matching:

    Advertiser
    Date
    Placement contains "Dedicated Eblast"
    """

    records = get_all_records()

    matches = []

    for record in records:

        fields = record.get("fields", {})

        record_advertiser = fields.get("Advertiser")
        record_date = fields.get("Date")
        placement = fields.get("Placement", [])

        # Airtable may return multi-select / linked fields as lists
        if isinstance(placement, str):
            placement = [placement]

        if (
            record_advertiser == advertiser
            and record_date == date
            and "Dedicated Eblast" in placement
        ):
            matches.append(record)

    return matches


if __name__ == "__main__":

    print("=" * 80)
    print("AIRTABLE DEDICATED EBLAST TEST")
    print("=" * 80)
    print()

    records = get_all_records()

    print(f"Total records retrieved: {len(records)}")
    print()

    dedicated_eblasts = []

    for record in records:

        fields = record.get("fields", {})

        placement = fields.get("Placement", [])

        if isinstance(placement, str):
            placement = [placement]

        if "Dedicated Eblast" in placement:
            dedicated_eblasts.append(record)

    print(
        f"Dedicated Eblast records: "
        f"{len(dedicated_eblasts)}"
    )

    print()
    print("DEDICATED EBLAST RECORDS")
    print("=" * 80)

    for record in dedicated_eblasts:

        fields = record.get("fields", {})

        print(
            f"Record ID:   {record.get('id')}\n"
            f"Advertiser:  {fields.get('Advertiser')}\n"
            f"Site:        {fields.get('Site')}\n"
            f"Date:        {fields.get('Date')}\n"
            f"Subscribers: {fields.get('Subscribers')}\n"
            f"Opens:       {fields.get('Total Opens')}\n"
            f"Clicks:      {fields.get('Total Clicks')}\n"
            f"URL:         {fields.get('URL')}\n"
            f"Placement:   {fields.get('Placement')}\n"
            f"{'-' * 80}"
        )