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


def test_apply_result_single_email_marks_empty_cells_nf():
    df = _df()
    csv_store.apply_result(df, 0, ["only@x.com"], [])
    assert df.at[0, config.COL_EMAIL1] == "only@x.com"
    assert df.at[0, config.COL_EMAIL2] == config.NOT_FOUND_MARK   # no 2nd email
    assert df.at[0, config.COL_PHONE] == config.NOT_FOUND_MARK    # no phone


def test_apply_result_marks_not_found_and_stamps_nf_everywhere():
    df = _df()
    csv_store.apply_result(df, 0, [], [])
    assert df.at[0, config.COL_STATUS] == "not_found"
    assert df.at[0, config.COL_EMAIL1] == config.NOT_FOUND_MARK
    assert df.at[0, config.COL_EMAIL2] == config.NOT_FOUND_MARK
    assert df.at[0, config.COL_PHONE] == config.NOT_FOUND_MARK


def test_found_data_does_not_get_overwritten_by_nf():
    df = _df()
    csv_store.apply_result(df, 0, ["a@x.com", "b@x.com"], ["+1 111 2222"])
    assert df.at[0, config.COL_EMAIL1] == "a@x.com"
    assert df.at[0, config.COL_EMAIL2] == "b@x.com"
    assert df.at[0, config.COL_PHONE] == "+1 111 2222"


def test_is_missing():
    df = _df()
    assert csv_store.is_missing(df.iloc[0]) is True
    assert csv_store.is_missing(df.iloc[1]) is False


def test_nf_row_is_not_missing_and_not_a_target():
    """A checked-but-empty row (all NF) must be skipped, not re-processed."""
    df = _df()
    csv_store.apply_result(df, 0, [], [])   # stamps NF across the row
    df.at[0, config.COL_STATUS] = ""          # even if status were lost...
    assert csv_store.is_missing(df.iloc[0]) is False   # ...NF alone skips it
    assert 0 not in csv_store.targets(df)
