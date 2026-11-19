/*
 Navicat SQLite Dump SQL

 Source Server         : Tip
 Source Server Type    : SQLite
 Source Server Version : 3045000 (3.45.0)
 Source Schema         : main

 Target Server Type    : SQLite
 Target Server Version : 3045000 (3.45.0)
 File Encoding         : 65001

 Date: 19/11/2026 20:35:36
*/

PRAGMA foreign_keys = false;

-- ----------------------------
-- Table structure for IndividualFundFlow_THS_Tiers
-- ----------------------------
DROP TABLE IF EXISTS "IndividualFundFlow_THS_Tiers";
CREATE TABLE "IndividualFundFlow_THS_Tiers" (
  "TradeDate" DATE NOT NULL,
  "StockCode" TEXT NOT NULL,
  "StockName" TEXT,
  "Price" real,
  "Rise" real,
  "SuperLarge" real,
  "Large" real,
  "Medium" real,
  "Small" real,
  "MainForce" real,
  PRIMARY KEY ("TradeDate", "StockCode")
);

PRAGMA foreign_keys = true;
