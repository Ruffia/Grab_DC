/*
 Navicat SQLite Dump SQL

 Source Server         : Tip
 Source Server Type    : SQLite
 Source Server Version : 3045000 (3.45.0)
 Source Schema         : main

 Target Server Type    : SQLite
 Target Server Version : 3045000 (3.45.0)
 File Encoding         : 65001

 Date: 19/11/2026 20:36:22
*/

PRAGMA foreign_keys = false;

-- ----------------------------
-- Table structure for Settings
-- ----------------------------
DROP TABLE IF EXISTS "Settings";
CREATE TABLE "Settings" (
  "ItemName" TEXT NOT NULL,
  "ItemValue" TEXT,
  "Description" TEXT,
  PRIMARY KEY ("ItemName")
);

-- ----------------------------
-- Records of Settings
-- ----------------------------
INSERT INTO "Settings" VALUES ('Days', '1', 'Grab_DC 项目获取多少天(交易日)的资金流数据');
INSERT INTO "Settings" VALUES ('Days_XQ', '1', 'Grab_XQ 项目获取多少天(交易日)的资金流数据');
INSERT INTO "Settings" VALUES ('DeltaPercent', '6.0', '净流入资金占近期最高值的百分比');
INSERT INTO "Settings" VALUES ('DeltaDays', '20', '统计多少天(交易日)的资金流数据');
INSERT INTO "Settings" VALUES ('Drop2LimitDays', '8', '在Drop2Limit 页面显示多少天的数据');

PRAGMA foreign_keys = true;
