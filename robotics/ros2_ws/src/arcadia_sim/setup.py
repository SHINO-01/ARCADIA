import os
from glob import glob

from setuptools import find_packages, setup

package_name = "arcadia_sim"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        (os.path.join("share", package_name, "worlds"), glob("worlds/*.wbt")),
        (os.path.join("share", package_name, "protos"), glob("protos/*.proto")),
        (os.path.join("share", package_name, "config"), glob("config/*.yaml")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="ARCADIA",
    maintainer_email="shachcha01@gmail.com",
    description="ARCADIA Webots controllers and ROS 2 nodes",
    license="MIT",
    entry_points={
        "console_scripts": [
            "robot_controller = arcadia_sim.robot_controller:main",
            "telemetry_throttle = arcadia_sim.telemetry_throttle:main",
            "fleet_diagnostics = arcadia_sim.fleet_diagnostics:main",
            "world_fetcher = arcadia_sim.world_fetcher:main",
        ],
    },
)
