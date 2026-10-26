from comptroller_automation.ai import _grounded
from comptroller_automation.extract import Location, _text_locations
from comptroller_automation.locations import distinct_locations, looks_like_directory, name_new_locations


def loc(street, zip_code="75013", name="", phone="", city="Allen"):
    return Location(street=street, city=city, state="TX", zip=zip_code, name=name, phone=phone)


def test_same_number_and_zip_merge_and_keep_phone():
    merged = distinct_locations([loc("3913 Broaddus Ave"), loc("3913 Broad Ave.", phone="9158523225")])
    assert len(merged) == 1 and merged[0].phone == "9158523225"


def test_text_location_takes_phone_after_address():
    lines = ["Allen Office", "1772 W. McDermott Dr, Suite 150, Allen, TX 75013", "Phone: (469) 340-2777"]
    (found,) = _text_locations(lines, "https://example.org")
    assert found.street == "1772 W. McDermott Dr, Suite 150" and found.phone == "4693402777"
    assert found.name == "Allen Office"


def test_city_labels_get_parent_name():
    (named,) = name_new_locations([loc("2150 S Central Expy", name="Mckinney Location:", city="Mckinney")],
                                  "HEALTHY MIND WORLD LLC")
    assert named.name == "HEALTHY MIND WORLD LLC - Mckinney"


def test_directory_detected_from_unrelated_names():
    listed = [loc(f"{n} Main St", name=f"Dr {w} Pediatrics") for n, w in ((1, "Smith"), (2, "Jones"), (3, "Lee"))]
    assert looks_like_directory(listed, "Texoma Care", "texomacare.com")
    assert not looks_like_directory([loc("1 Main St", name="Texoma Care Bonham")], "Texoma Care", "texomacare.com")


def test_ai_values_must_be_on_the_page():
    page = "Visit us at 5728 LBJ Fwy, Suite 250, Dallas, TX 75240. Call 214.363.4420"
    good, dropped = _grounded({"street": "5728 LBJ Fwy, Suite 250", "city": "Dallas", "state": "Texas",
                               "zip": "75240", "phone": "214-363-4420", "fax": "214-000-0000",
                               "email": "made@up.com", "name": ""}, page, "u")
    assert good.phone == "2143634420" and good.state == "TX"
    assert good.fax == "" and good.email == "" and dropped == 2
    invented, _ = _grounded({"street": "12 Fake Rd", "city": "Dallas", "state": "TX", "zip": "75240",
                             "phone": "", "fax": "", "email": "", "name": ""}, page, "u")
    assert invented is None
