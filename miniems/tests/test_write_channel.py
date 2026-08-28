"""write_channel.py: generalised write-confirm/retry primitives."""
import pytest

from write_channel import WriteChannel, WriteChannelSet, WriteSpec, matches


class TestMatchesSwitch:
    def test_exact_string_match(self):
        spec = WriteSpec(domain="switch", service="turn_on", entity_id="switch.x",
                          payload={}, expected="on")
        assert matches("on", spec) is True

    def test_mismatch(self):
        spec = WriteSpec(domain="switch", service="turn_on", entity_id="switch.x",
                          payload={}, expected="on")
        assert matches("off", spec) is False

    def test_none_observed_does_not_match(self):
        spec = WriteSpec(domain="switch", service="turn_on", entity_id="switch.x",
                          payload={}, expected="on")
        assert matches(None, spec) is False


class TestMatchesSelect:
    def test_exact_option_match(self):
        spec = WriteSpec(domain="select", service="select_option", entity_id="select.x",
                          payload={}, expected="Lithium")
        assert matches("Lithium", spec) is True

    def test_option_mismatch(self):
        spec = WriteSpec(domain="select", service="select_option", entity_id="select.x",
                          payload={}, expected="Lithium")
        assert matches("Lead-Acid", spec) is False


class TestMatchesNumber:
    def test_within_tolerance_matches(self):
        spec = WriteSpec(domain="number", service="set_value", entity_id="number.x",
                          payload={}, expected=185, tolerance=0.5)
        assert matches(185.2, spec) is True

    def test_outside_tolerance_does_not_match(self):
        spec = WriteSpec(domain="number", service="set_value", entity_id="number.x",
                          payload={}, expected=185, tolerance=0.5)
        assert matches(184.0, spec) is False

    def test_exact_match(self):
        spec = WriteSpec(domain="number", service="set_value", entity_id="number.x",
                          payload={}, expected=42)
        assert matches(42, spec) is True

    def test_none_observed_does_not_match(self):
        spec = WriteSpec(domain="number", service="set_value", entity_id="number.x",
                          payload={}, expected=42)
        assert matches(None, spec) is False

    def test_unparsable_observed_does_not_match(self):
        spec = WriteSpec(domain="number", service="set_value", entity_id="number.x",
                          payload={}, expected=42)
        assert matches("not-a-number", spec) is False

    def test_unparsable_expected_does_not_match(self):
        spec = WriteSpec(domain="number", service="set_value", entity_id="number.x",
                          payload={}, expected="also-not-a-number")
        assert matches(42.0, spec) is False

    def test_string_observed_is_coerced(self):
        """HA state values arrive as strings – "185" must match 185."""
        spec = WriteSpec(domain="number", service="set_value", entity_id="number.x",
                          payload={}, expected=185)
        assert matches("185", spec) is True


class TestWriteChannel:
    def test_defaults_are_confirmed_with_no_target(self):
        ch = WriteChannel()
        assert ch.confirmed is True
        assert ch.target is None
        assert ch.sent_at is None
        assert ch.pending_since is None
        assert ch.label == ""


class TestWriteChannelSet:
    def test_creates_one_channel_per_name(self):
        channels = WriteChannelSet(["a", "b", "c"])
        assert channels["a"].confirmed is True
        assert channels["b"].confirmed is True
        assert channels["c"].confirmed is True

    def test_unknown_name_raises_keyerror(self):
        channels = WriteChannelSet(["a"])
        with pytest.raises(KeyError):
            channels["nonexistent"]

    def test_iteration_yields_all_channels(self):
        channels = WriteChannelSet(["a", "b"])
        assert len(list(channels)) == 2

    def test_unconfirmed_count_zero_when_all_confirmed(self):
        channels = WriteChannelSet(["a", "b"])
        assert channels.unconfirmed_count == 0

    def test_unconfirmed_count_reflects_pending_channels(self):
        channels = WriteChannelSet(["a", "b", "c"])
        channels["a"].confirmed = False
        channels["b"].confirmed = False
        assert channels.unconfirmed_count == 2

    def test_longest_pending_sec_zero_when_all_confirmed(self):
        channels = WriteChannelSet(["a"])
        assert channels.longest_pending_sec == 0.0

    def test_longest_pending_sec_reflects_the_oldest_pending_channel(self):
        import time
        channels = WriteChannelSet(["a", "b"])
        channels["a"].confirmed = False
        channels["a"].pending_since = time.monotonic() - 100
        channels["b"].confirmed = False
        channels["b"].pending_since = time.monotonic() - 10
        assert channels.longest_pending_sec == pytest.approx(100, abs=1)

    def test_stuck_channel_labels_empty_when_all_confirmed(self):
        channels = WriteChannelSet(["a"])
        assert channels.stuck_channel_labels == []

    def test_stuck_channel_labels_longest_pending_first(self):
        import time
        channels = WriteChannelSet(["a", "b"])
        channels["a"].confirmed = False
        channels["a"].pending_since = time.monotonic() - 10
        channels["a"].label = "Channel A"
        channels["b"].confirmed = False
        channels["b"].pending_since = time.monotonic() - 100
        channels["b"].label = "Channel B"
        assert channels.stuck_channel_labels == ["Channel B", "Channel A"]

    def test_stuck_channel_without_a_label_is_excluded(self):
        channels = WriteChannelSet(["a"])
        channels["a"].confirmed = False
        channels["a"].pending_since = 1.0
        channels["a"].label = ""
        assert channels.stuck_channel_labels == []

    def test_reset_replaces_every_channel_with_a_fresh_one(self):
        channels = WriteChannelSet(["a", "b"])
        channels["a"].confirmed = False
        channels["a"].target = 42
        channels.reset()
        assert channels["a"].confirmed is True
        assert channels["a"].target is None
        assert channels["b"].confirmed is True
