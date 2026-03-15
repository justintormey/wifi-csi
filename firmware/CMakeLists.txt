# WiFi CSI People Tracking — ESP32-S3 Firmware
# Top-level ESP-IDF project CMakeLists.txt
#
# Build for TX or RX mode via menuconfig or sdkconfig.defaults:
#   CONFIG_CSI_BOARD_ROLE_TX=y   (TX board)
#   CONFIG_CSI_BOARD_ROLE_RX=y   (RX board, default)

cmake_minimum_required(VERSION 3.16)

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(wifi_csi_firmware)
