#!/usr/bin/env bash
# ARCADIA — native ROS 2 Lyrical Luth install for Ubuntu 26.04 (resolute).
# Run with: sudo bash robotics/scripts/install_ros2_apt.sh
# Installs a lean ros-base + the packages ARCADIA needs (no containers, per hardware constraints).
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Please run with sudo: sudo bash $0" >&2
  exit 1
fi

CODENAME="$(. /etc/os-release && echo "${UBUNTU_CODENAME}")"
ROS_DISTRO="${ROS_DISTRO:-lyrical}"

echo "==> Ubuntu codename: ${CODENAME}, ROS distro: ${ROS_DISTRO}"
export DEBIAN_FRONTEND=noninteractive

apt-get update -y
apt-get install -y software-properties-common curl gnupg lsb-release locales
add-apt-repository -y universe || true
locale-gen en_US en_US.UTF-8 >/dev/null 2>&1 || true

echo "==> Adding ROS 2 apt source"
ROS_APT_SOURCE_VERSION="$(curl -fsSL https://api.github.com/repos/ros-infrastructure/ros-apt-source/releases/latest | grep -F '"tag_name"' | awk -F'"' '{print $4}')"
DEB="/tmp/ros2-apt-source.deb"
curl -fsSL -o "${DEB}" "https://github.com/ros-infrastructure/ros-apt-source/releases/download/${ROS_APT_SOURCE_VERSION}/ros2-apt-source_${ROS_APT_SOURCE_VERSION}.${CODENAME}_all.deb"
dpkg -i "${DEB}"
apt-get update -y

echo "==> Installing ROS 2 ${ROS_DISTRO} (ros-base) + ARCADIA dependencies"
apt-get install -y \
  ros-${ROS_DISTRO}-ros-base \
  ros-${ROS_DISTRO}-rosbridge-server \
  ros-${ROS_DISTRO}-rosapi \
  ros-${ROS_DISTRO}-diagnostic-aggregator \
  ros-${ROS_DISTRO}-diagnostic-updater \
  ros-${ROS_DISTRO}-diagnostic-msgs \
  ros-${ROS_DISTRO}-nav2-msgs \
  ros-${ROS_DISTRO}-topic-tools \
  ros-${ROS_DISTRO}-tf2-ros \
  ros-${ROS_DISTRO}-tf2-ros-py \
  ros-${ROS_DISTRO}-tf-transformations \
  ros-${ROS_DISTRO}-rmw-cyclonedds-cpp \
  ros-${ROS_DISTRO}-rosidl-default-generators \
  ros-${ROS_DISTRO}-ament-cmake \
  ros-${ROS_DISTRO}-ament-lint-auto \
  ros-${ROS_DISTRO}-launch-xml \
  ros-${ROS_DISTRO}-nav-msgs \
  ros-${ROS_DISTRO}-sensor-msgs \
  ros-${ROS_DISTRO}-geometry-msgs \
  ros-${ROS_DISTRO}-std-srvs \
  ros-dev-tools \
  python3-colcon-common-extensions \
  python3-rosdep \
  python3-vcstool \
  python3-numpy \
  python3-yaml \
  python3-requests \
  python3-websockets \
  build-essential cmake git

# rviz2 is optional (useful for debugging, ~300MB). Set WITH_RVIZ=1 to include it.
if [[ "${WITH_RVIZ:-0}" == "1" ]]; then
  apt-get install -y ros-${ROS_DISTRO}-rviz2
fi

echo "==> Initialising rosdep"
rosdep init >/dev/null 2>&1 || true
if [[ -n "${SUDO_USER:-}" ]]; then
  su - "${SUDO_USER}" -c "rosdep update" || true
fi

cat <<MSG

ROS 2 ${ROS_DISTRO} installed natively.
Next, as your normal user:
    source /opt/ros/${ROS_DISTRO}/setup.bash
    make robotics-build      # builds the ARCADIA ROS 2 workspace
    make sim                 # launches Webots + fleet + rosbridge
MSG
