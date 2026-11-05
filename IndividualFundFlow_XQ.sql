/*
 Navicat SQLite Dump SQL

 Source Server         : Tip
 Source Server Type    : SQLite
 Source Server Version : 3045000 (3.45.0)
 Source Schema         : main

 Target Server Type    : SQLite
 Target Server Version : 3045000 (3.45.0)
 File Encoding         : 65001

 Date: 05/11/2026 20:08:28
*/

PRAGMA foreign_keys = false;

-- ----------------------------
-- Table structure for IndividualFundFlow_XQ
-- ----------------------------
DROP TABLE IF EXISTS "IndividualFundFlow_XQ";
CREATE TABLE "IndividualFundFlow_XQ" (
  "TradeDate" DATE NOT NULL,
  "StockCode" TEXT NOT NULL,
  "StockName" TEXT,
  "Price" real,
  "Rise" real,
  "FundFlow_MainForce" real,
  PRIMARY KEY ("TradeDate", "StockCode")
);

PRAGMA foreign_keys = true;
