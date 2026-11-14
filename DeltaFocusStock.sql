/*
 Navicat SQLite Dump SQL

 Source Server         : Tip
 Source Server Type    : SQLite
 Source Server Version : 3045000 (3.45.0)
 Source Schema         : main

 Target Server Type    : SQLite
 Target Server Version : 3045000 (3.45.0)
 File Encoding         : 65001

 Date: 14/11/2026 17:48:34
*/

PRAGMA foreign_keys = false;

-- ----------------------------
-- Table structure for DeltaFocusStock
-- ----------------------------
DROP TABLE IF EXISTS "DeltaFocusStock";
CREATE TABLE "DeltaFocusStock" (
  "FocusDate" DATE NOT NULL,
  "StockCode" TEXT NOT NULL,
  "StockName" TEXT,
  PRIMARY KEY ("FocusDate", "StockCode")
);

-- ----------------------------
-- Records of DeltaFocusStock
-- ----------------------------

PRAGMA foreign_keys = true;
