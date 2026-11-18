/*
 Navicat SQLite Dump SQL

 Source Server         : Tip
 Source Server Type    : SQLite
 Source Server Version : 3045000 (3.45.0)
 Source Schema         : main

 Target Server Type    : SQLite
 Target Server Version : 3045000 (3.45.0)
 File Encoding         : 65001

 Date: 18/11/2026 10:50:28
*/

PRAGMA foreign_keys = false;

-- ----------------------------
-- Table structure for IndividualFundFlow_THS
-- ----------------------------
DROP TABLE IF EXISTS "IndividualFundFlow_THS";
CREATE TABLE "IndividualFundFlow_THS" (
  "TradeDate" DATE NOT NULL,
  "StockCode" TEXT NOT NULL,
  "StockName" TEXT,
  "Price" real,
  "Rise" real,
  "FundFlow_MainForce" real,
  PRIMARY KEY ("TradeDate", "StockCode")
);

PRAGMA foreign_keys = true;
