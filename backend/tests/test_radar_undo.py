"""Radar undo help (11.30): the command to reverse a costly change, never run by us."""

from __future__ import annotations

from ccradar.changes.undo import undo


def test_reverse_commands_for_added_cost() -> None:
    run = undo("RunInstances", "i-0abc", "ap-south-1", 120.0, "2 x m7i.large")
    assert run is not None and run["command"] == (
        "aws ec2 terminate-instances --region ap-south-1 --instance-ids i-0abc"
    )
    assert run["console"].startswith("https://ap-south-1.console.aws.amazon.com/ec2/")
    resize = undo("ModifyInstanceAttribute", "i-0abc", "us-east-1", 50.0, "m5.large -> m5.4xlarge")
    assert resize is not None and "--instance-type Value=m5.large" in resize["command"]
    assert resize["command"].count("\n") == 3  # stop, wait, modify, start
    db = undo("ModifyDBInstance", "orders", "eu-west-1", 300.0, "db.r6g.large -> db.r6g.2xlarge")
    assert db is not None and db["command"].endswith("--db-instance-class db.r6g.large")
    rds = undo("CreateDBInstance", "orders", "eu-west-1", 300.0, "db.r6g.large")
    assert rds is not None and "--final-db-snapshot-identifier orders-final" in rds["command"]
    grown = undo("ModifyVolume", "vol-1", "us-east-1", 20.0, "volume now 500 GB gp3")
    assert grown is not None and "command" not in grown and "cannot shrink" in grown["text"]


def test_no_undo_for_savings_unknown_or_unsafe_input() -> None:
    assert undo("TerminateInstances", "i-0abc", "us-east-1", -80.0, "terminated 1") is None
    assert undo("RunInstances", "i-0abc", "us-east-1", -1.0, None) is None
    assert undo("PutObject", "x", "us-east-1", 10.0, None) is None
    assert undo("RunInstances", "ami-123", "us-east-1", 10.0, None) is None  # not an instance
    assert undo("RunInstances", "i-1; rm -rf /", "us-east-1", 10.0, None) is None
    assert undo("RunInstances", "i-1", "us-east-1; reboot", 10.0, None) is None
    assert undo("CreateDBInstance", "x$(id)", "us-east-1", 10.0, None) is None


def test_same_event_name_differs_by_service() -> None:
    redshift = undo("CreateCluster", "warehouse", "us-east-1", 900.0, "2 x ra3.4xlarge Redshift",
                    "redshift.amazonaws.com")  # fmt: skip
    assert redshift is not None and redshift["command"].startswith("aws redshift delete-cluster")
    eks = undo("CreateCluster", "prod", "us-east-1", 73.0, "EKS control plane", "eks.amazonaws.com")
    assert eks is not None and "command" not in eks
    asg = undo("SetDesiredCapacity", "web", "us-east-1", 300.0, "2 -> 6 x m5.large",
               "autoscaling.amazonaws.com")  # fmt: skip
    assert asg is not None and asg["command"].endswith("--desired-capacity 2")
    back = undo("ResizeCluster", "warehouse", "us-east-1", 500.0,
                "2 x ra3.xlplus -> 4 x ra3.xlplus", "redshift.amazonaws.com")  # fmt: skip
    assert back is not None and "--number-of-nodes 2" in back["command"]
