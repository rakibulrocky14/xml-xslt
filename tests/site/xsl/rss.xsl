<?xml version="1.0" encoding="UTF-8"?>
<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
  xmlns:exsl="http://exslt.org/common" xmlns:str="http://exslt.org/strings" extension-element-prefixes="exsl str">
  <xsl:import href="base.xsl"/>
  <xsl:include href="common.xsl"/>
  <xsl:output method="html" encoding="UTF-8" doctype-system="about:legacy-compat"/>
  <xsl:variable name="meta" select="document('../data/meta.xml')/meta"/>
  <xsl:template match="/rss/channel">
    <html><head><title><xsl:value-of select="title"/></title></head>
    <body>
      <xsl:apply-templates select="title"/>
      <p class="site"><xsl:value-of select="$meta/site"/></p>
      <xsl:variable name="items"><xsl:for-each select="item"><i><xsl:value-of select="title"/></i></xsl:for-each></xsl:variable>
      <p class="count"><xsl:value-of select="count(exsl:node-set($items)/i)"/> items</p>
      <ul><xsl:for-each select="item"><li>
        <a href="{link}"><xsl:value-of select="title"/></a>
        <xsl:call-template name="badge"><xsl:with-param name="t" select="str:padding(2, '*')"/></xsl:call-template>
      </li></xsl:for-each></ul>
    </body></html>
  </xsl:template>
  <xsl:template match="/"><xsl:apply-templates select="rss/channel"/></xsl:template>
</xsl:stylesheet>
