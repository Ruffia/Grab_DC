/*
 Navicat SQLite Dump SQL

 Source Server         : Tip
 Source Server Type    : SQLite
 Source Server Version : 3045000 (3.45.0)
 Source Schema         : main

 Target Server Type    : SQLite
 Target Server Version : 3045000 (3.45.0)
 File Encoding         : 65001

 Date: 04/11/2026 21:41:45
*/

PRAGMA foreign_keys = false;

-- ----------------------------
-- Table structure for IndividualFundFlow_DC
-- ----------------------------
DROP TABLE IF EXISTS "IndividualFundFlow_DC";
CREATE TABLE "IndividualFundFlow_DC" (
  "TradeDate" DATE NOT NULL,
  "StockCode" TEXT NOT NULL,
  "StockName" TEXT,
  "Price" real,
  "Rise" real,
  "FundFlow_MainForce" real,
  "FundFlow_SuperLargeOrder" real,
  "FundFlow_LargeOrder" real,
  "FundFlow_MediumOrder" real,
  "FundFlow_SmallOrder" real,
  PRIMARY KEY ("TradeDate", "StockCode")
);

PRAGMA foreign_keys = true;
