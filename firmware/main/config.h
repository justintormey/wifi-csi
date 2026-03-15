/**
 * WiFi CSI Firmware — Compile-time Configuration
 *
 * All values are pulled from Kconfig (menuconfig / sdkconfig.defaults).
 * Override per board by running `idf.py menuconfig` or editing sdkconfig.
 */

#pragma once

#include "sdkconfig.h"

/* ── Board Role ─────────────────────────────────────────────────────── */

#ifdef CONFIG_CSI_BOARD_ROLE_TX
#define BOARD_ROLE_TX  1
#define BOARD_ROLE_RX  0
#else
#define BOARD_ROLE_TX  0
#define BOARD_ROLE_RX  1
#endif

/* ── WiFi ───────────────────────────────────────────────────────────── */

#define WIFI_SSID          CONFIG_CSI_WIFI_SSID
#define WIFI_PASSWORD      CONFIG_CSI_WIFI_PASSWORD
#define WIFI_CHANNEL       CONFIG_CSI_WIFI_CHANNEL

/* ── Floor ──────────────────────────────────────────────────────────── */

#define FLOOR_ID           CONFIG_CSI_FLOOR_ID

/* ── MQTT ───────────────────────────────────────────────────────────── */

#define MQTT_BROKER_IP     CONFIG_CSI_MQTT_BROKER_IP
#define MQTT_BROKER_PORT   CONFIG_CSI_MQTT_BROKER_PORT

/* ── TX Mode ────────────────────────────────────────────────────────── */

#define TX_RATE_HZ         CONFIG_CSI_TX_RATE_HZ
#define TX_INTERVAL_US     (1000000 / TX_RATE_HZ)
#define TX_TARGET_IP       CONFIG_CSI_TX_TARGET_IP
#define TX_UDP_PORT        CONFIG_CSI_TX_UDP_PORT

/* ── Board Identity ─────────────────────────────────────────────────── */

#define BOARD_ID           CONFIG_CSI_BOARD_ID

/* ── CSI Constants ──────────────────────────────────────────────────── */

/** HT40 mode provides 114 subcarriers of I/Q data per frame. */
#define CSI_NUM_SUBCARRIERS  114

/**
 * Binary packet size sent over MQTT.
 * Header: uint64 timestamp (8) + 6B tx_mac + 6B rx_mac + int8 rssi (1) + uint8 floor_id (1) = 22
 * I/Q:    114 subcarriers × 2 (I,Q) × 2 bytes (int16) = 456
 * Total:  478 bytes
 */
#define CSI_PACKET_SIZE      478
#define CSI_HEADER_SIZE      22
#define CSI_IQ_SIZE          456
