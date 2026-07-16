import pandas as pd

from wiza import config, csv_store


def _df():
    return pd.DataFrame([
        # 0: missing everything, unprocessed -> target
        {config.COL_URL: "u1", config.COL_NAME: "A", config.COL_EMAIL1: "",
         config.COL_EMAIL2: "", config.COL_PHONE: "", config.COL_STATUS: ""},
        # 1: already has an email -> not a target
        {config.COL_URL: "u2", config.COL_NAME: "B", config.COL_EMAIL1: "has@x.com",
         config.COL_EMAIL2: "", config.COL_PHONE: "", config.COL_STATUS: ""},
        # 2: missing but already marked done -> skip
        {config.COL_URL: "u3", config.COL_NAME: "C", config.COL_EMAIL1: "",
         config.COL_EMAIL2: "", config.COL_PHONE: "", config.COL_STATUS: "done"},
    ])


def test_targets_only_missing_and_unprocessed():
    assert csv_store.targets(_df()) == [0]


def test_apply_result_maps_two_emails_and_one_phone():
    df = _df()
    csv_store.apply_result(df, 0, ["a@x.com", "b@x.com", "c@x.com"], ["+1 111 2222", "+1 333"])
    assert df.at[0, config.COL_EMAIL1] == "a@x.com"
    assert df.at[0, config.COL_EMAIL2] == "b@x.com"        # 3rd email dropped
    assert df.at[0, config.COL_PHONE] == "+1 111 2222"      # 2nd phone dropped
    assert df.at[0, config.COL_STATUS] == "done"


def test_apply_result_single_email_leaves_email2_empty():
    df = _df()
    csv_store.apply_result(df, 0, ["only@x.com"], [])
    assert df.at[0, config.COL_EMAIL1] == "only@x.com"
    assert df.at[0, config.COL_EMAIL2] == ""
    assert df.at[0, config.COL_PHONE] == ""


def test_apply_result_marks_not_found_when_empty():
    df = _df()
    csv_store.apply_result(df, 0, [], [])
    assert df.at[0, config.COL_STATUS] == "not_found"


def test_is_missing():
    df = _df()
    assert csv_store.is_missing(df.iloc[0]) is True
    assert csv_store.is_missing(df.iloc[1]) is False
