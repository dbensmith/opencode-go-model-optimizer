"""Tests for opencode-go-model-optimizer CLI."""

from opencode_go_model_optimizer.cli import main, parse_args


def test_parse_args_select():
    args = parse_args(["select", "--format", "json", "--mode", "allowance"])
    assert args.command == "select"
    assert args.format == "json"
    assert args.mode == "allowance"


def test_main_no_args(capsys):
    rc = main([])
    assert rc == 0
    captured = capsys.readouterr()
    assert "use --help" in captured.err
