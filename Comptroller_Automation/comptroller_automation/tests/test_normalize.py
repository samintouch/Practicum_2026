from comptroller_automation.normalize import phone_digits, same_location, social_key, street_key


def test_phone_digits():
    assert phone_digits("(915) 852-3225") == "9158523225"
    assert phone_digits("+1 915.852.3225") == "9158523225"
    assert phone_digits("852-3225") == ""


def test_street_key_drops_suite_and_abbreviates():
    assert street_key("16633 North Dallas Parkway, Suite 150") == "16633 N DALLAS PKWY"


def test_same_location():
    on_file = {"street": "4100 FAIRWAY DR STE 200", "zip": "75010"}
    assert same_location(on_file, {"street": "4100 Fairway Drive #200", "zip": "75010"})
    assert not same_location(on_file, {"street": "4200 Fairway Drive", "zip": "75010"})


def test_social_key():
    assert social_key("https://www.instagram.com/addisonpain/") == "instagram:addisonpain"
    assert social_key("https://x.com/Metrocare") == social_key("https://twitter.com/metrocare")
    assert social_key("https://www.facebook.com/sharer/sharer.php?u=x") is None
    assert social_key("https://www.instagram.com/p/abc123/") is None
    assert social_key("https://www.youtube.com/watch?v=abc") is None
    assert social_key("https://www.tiktok.com/@allenpsychiatry") == "tiktok:allenpsychiatry"
    assert social_key("https://wa.me/19155551234") == "whatsapp:19155551234"
    assert social_key("@addisonpain", "instagram") == "instagram:addisonpain"


def test_name_forms():
    from comptroller_automation.normalize import name_similarity
    from comptroller_automation.verify import _same_distinctive_words
    assert name_similarity("JOHN PETER SMITH HEALTH NETWORK FAMILY HEALTH CENTER", "JPS Health Network") == 1.0
    assert name_similarity("QUAZI IMAM MD", "Quazi Imam, M.D.") == 1.0
    assert _same_distinctive_words("HUANG MEDICAL CLINIC", "Wentian Huang MD, PhD, PC")
    assert _same_distinctive_words("TRIMED BEHAVIORAL AND SLEEP MEDICINE", "Tri-MED Integrative Psychiatry & Sleep Medicine")
    assert not _same_distinctive_words("INTERVENTIONAL PAIN AND WELLNESS CENTER PLLC", "The Pain and Spine Specialists")
