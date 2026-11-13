cmake_minimum_required(VERSION 3.16)
project(arcadia_supervisor)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)
find_package(arcadia_msgs REQUIRED)
find_package(std_msgs REQUIRED)

# ---- Webots C++ controller library (snap or tarball install)
if(NOT DEFINED ENV{WEBOTS_HOME})
  if(EXISTS /snap/webots/current/usr/share/webots/include/controller/cpp)
    set(WEBOTS_HOME /snap/webots/current/usr/share/webots)
  elseif(EXISTS /usr/local/webots/include/controller/cpp)
    set(WEBOTS_HOME /usr/local/webots)
  else()
    message(FATAL_ERROR "Webots not found: set WEBOTS_HOME")
  endif()
else()
  set(WEBOTS_HOME $ENV{WEBOTS_HOME})
endif()
message(STATUS "Using Webots at ${WEBOTS_HOME}")
# Resolve the snap symlink to a stable revision path so the RPATH keeps working.
get_filename_component(WEBOTS_HOME_REAL "${WEBOTS_HOME}" REALPATH)

add_executable(supervisor src/supervisor.cpp src/world_spawner.cpp src/ground_truth.cpp)
target_include_directories(supervisor PRIVATE
  include
  ${WEBOTS_HOME_REAL}/include/controller/cpp
  ${WEBOTS_HOME_REAL}/include/controller/c
  third_party)
target_link_directories(supervisor PRIVATE ${WEBOTS_HOME_REAL}/lib/controller)
target_link_libraries(supervisor CppController Controller rclcpp::rclcpp ${arcadia_msgs_TARGETS} ${std_msgs_TARGETS})
set_target_properties(supervisor PROPERTIES
  INSTALL_RPATH "${WEBOTS_HOME_REAL}/lib/controller;${WEBOTS_HOME}/lib/controller"
  BUILD_RPATH "${WEBOTS_HOME_REAL}/lib/controller")

install(TARGETS supervisor DESTINATION lib/${PROJECT_NAME})
ament_package()
