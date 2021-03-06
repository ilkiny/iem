#!/usr/bin/env python
"""Generate a shapefile of warnings based on the CGI request"""
import zipfile
import os
import shutil
import cgi
import sys
import datetime
from collections import OrderedDict

import pytz
import psycopg2
from geopandas import GeoDataFrame


def get_time_extent(form):
    """ Figure out the time extent of this request"""
    if 'year' in form:
        year1 = form.getfirst('year')
        year2 = form.getfirst('year')
    else:
        year1 = form.getfirst('year1')
        year2 = form.getfirst('year2')
    month1 = form.getfirst('month1')
    month2 = form.getfirst('month2')
    day1 = form.getfirst('day1')
    day2 = form.getfirst('day2')
    hour1 = form.getfirst('hour1')
    hour2 = form.getfirst('hour2')
    minute1 = form.getfirst('minute1')
    minute2 = form.getfirst('minute2')
    sts = datetime.datetime(int(year1), int(month1), int(day1),
                            int(hour1), int(minute1))
    sts = sts.replace(tzinfo=pytz.timezone('UTC'))
    ets = datetime.datetime(int(year2), int(month2), int(day2),
                            int(hour2), int(minute2))
    ets = ets.replace(tzinfo=pytz.timezone('UTC'))
    return sts, ets


def parse_wfo_location_group(form):
    """Parse wfoLimiter"""
    limiter = ''
    if 'wfo[]' in form:
        wfos = form.getlist('wfo[]')
        wfos.append('XXX')  # Hack to make next section work
        if 'ALL' not in wfos:
            limiter = " and w.wfo in %s " % (str(tuple(wfos)),)

    if 'wfos[]' in form:
        wfos = form.getlist('wfos[]')
        wfos.append('XXX')  # Hack to make next section work
        if 'ALL' not in wfos:
            limiter = " and w.wfo in %s " % (str(tuple(wfos)),)
    return limiter


def send_error(msg):
    """Error out please!"""
    sys.stdout.write("Content-type: text/plain\n\n")
    sys.stdout.write("ERROR: %s" % (msg, ))
    sys.exit()


def main():
    """Go Main Go"""
    form = cgi.FieldStorage()
    sts, ets = get_time_extent(form)

    location_group = form.getfirst('location_group', 'wfo')
    if location_group == 'states':
        if 'states[]' in form:
            states = form.getlist('states[]')
            states.append('XX')  # Hack for 1 length
            wfo_limiter = ''
            wfo_limiter2 = (" and substr(w.ugc, 1, 2) in %s "
                            ) % (str(tuple(states)),)
        else:
            send_error('No state specified')
    elif location_group == 'wfo':
        wfo_limiter = parse_wfo_location_group(form)
        wfo_limiter2 = wfo_limiter
    else:
        # Unknown location_group
        send_error('Unknown location_group (%s)' % (location_group, ))

    # Change to postgis db once we have the wfo list
    pgconn = psycopg2.connect(database='postgis', host='iemdb', user='nobody')
    fn = "wwa_%s_%s" % (sts.strftime("%Y%m%d%H%M"), ets.strftime("%Y%m%d%H%M"))
    timeopt = int(form.getfirst('timeopt', [1])[0])
    if timeopt == 2:
        year3 = int(form.getfirst('year3'))
        month3 = int(form.getfirst('month3'))
        day3 = int(form.getfirst('day3'))
        hour3 = int(form.getfirst('hour3'))
        minute3 = int(form.getfirst('minute3'))
        sts = datetime.datetime(year3, month3, day3, hour3, minute3)
        sts = sts.replace(tzinfo=pytz.timezone("UTC"))
        fn = "wwa_%s" % (sts.strftime("%Y%m%d%H%M"), )

    os.chdir("/tmp/")
    for suffix in ['shp', 'shx', 'dbf', 'txt', 'zip']:
        if os.path.isfile("%s.%s" % (fn, suffix)):
            os.remove("%s.%s" % (fn, suffix))

    limiter = ""
    if 'limit0' in form:
        limiter += (
            " and phenomena IN ('TO','SV','FF','MA') and significance = 'W' ")

    sbwlimiter = " WHERE gtype = 'P' " if 'limit1' in form else ""

    warnings_table = "warnings"
    sbw_table = "sbw"
    if sts.year == ets.year:
        warnings_table = "warnings_%s" % (sts.year,)
        if sts.year > 2001:
            sbw_table = "sbw_%s" % (sts.year,)
        else:
            sbw_table = 'sbw_2014'

    geomcol = "geom"
    if form.getfirst('simple', 'no') == 'yes':
        geomcol = "simple_geom"

    cols = """geo, wfo, utc_issue as issued, utc_expire as expired,
     utc_prodissue as init_iss, utc_init_expire as init_exp,
     phenomena as phenom,
     gtype, significance as sig, eventid as etn,  status, ugc as nws_ugc,
     area2d as area_km2"""

    timelimit = "issue >= '%s' and issue < '%s'" % (sts, ets)
    if timeopt == 2:
        timelimit = "issue <= '%s' and issue > '%s' and expire > '%s'" % (
            sts, sts + datetime.timedelta(days=-30), sts)

    sql = """
    WITH stormbased as (
     SELECT geom as geo, 'P'::text as gtype, significance, wfo,
     status, eventid, ''::text as ugc,
     phenomena,
     ST_area( ST_transform(w.geom,2163) ) / 1000000.0 as area2d,
     to_char(expire at time zone 'UTC', 'YYYYMMDDHH24MI') as utc_expire,
     to_char(issue at time zone 'UTC', 'YYYYMMDDHH24MI') as utc_issue,
     to_char(issue at time zone 'UTC', 'YYYYMMDDHH24MI') as utc_prodissue,
     to_char(init_expire at time zone 'UTC',
             'YYYYMMDDHH24MI') as utc_init_expire
     from %(sbw_table)s w WHERE status = 'NEW' and %(timelimit)s
     %(wfo_limiter)s %(limiter)s
    ),
    countybased as (
     SELECT u.%(geomcol)s as geo, 'C'::text as gtype,
     significance,
     w.wfo, status, eventid, u.ugc, phenomena,
     u.area2163 as area2d,
     to_char(expire at time zone 'UTC', 'YYYYMMDDHH24MI') as utc_expire,
     to_char(issue at time zone 'UTC', 'YYYYMMDDHH24MI') as utc_issue,
     to_char(product_issue at time zone 'UTC',
             'YYYYMMDDHH24MI') as utc_prodissue,
     to_char(init_expire at time zone 'UTC',
             'YYYYMMDDHH24MI') as utc_init_expire
     from %(warnings_table)s w JOIN ugcs u on (u.gid = w.gid) WHERE
     %(timelimit)s %(wfo_limiter2)s %(limiter)s
     )
     SELECT %(cols)s from stormbased UNION ALL
     SELECT %(cols)s from countybased %(sbwlimiter)s
    """ % dict(sbw_table=sbw_table,
               timelimit=timelimit,
               wfo_limiter=wfo_limiter,
               limiter=limiter,
               geomcol=geomcol,
               warnings_table=warnings_table,
               wfo_limiter2=wfo_limiter2,
               cols=cols,
               sbwlimiter=sbwlimiter)
    # dump SQL to disk for further debugging
    # o = open('/tmp/daryl.txt', 'w')
    # o.write(sql)
    # o.close()

    df = GeoDataFrame.from_postgis(sql, pgconn, 'geo')
    if len(df.index) == 0:
        sys.stdout.write("Content-type: text/plain\n\n")
        sys.stdout.write("ERROR: No results found for query, please try again")
        sys.exit()

    # Capitolize columns please
    df.columns = [s.upper() if s != 'geo' else s for s in df.columns.values]
    schema = {'geometry': 'Polygon',
              'properties': OrderedDict(
                  [(u'WFO', 'str:3'),
                   (u'ISSUED', 'str:12'),
                   (u'EXPIRED', 'str:12'),
                   (u'INIT_ISS', 'str:12'),
                   (u'INIT_EXP', 'str:12'),
                   (u'PHENOM', 'str:2'),
                   (u'GTYPE', 'str:1'),
                   (u'SIG', 'str:1'),
                   (u'ETN', 'str:4'),
                   (u'STATUS', 'str:3'),
                   (u'NWS_UGC', 'str:6'),
                   (u'AREA_KM2', 'float:24.15')])}
    df.to_file(fn + ".shp", schema=schema)

    shutil.copyfile("/opt/iem/data/gis/meta/4326.prj", fn + ".prj")

    zf = zipfile.ZipFile(fn + ".zip", 'w', zipfile.ZIP_DEFLATED)
    zf.write(fn + ".shp")
    zf.write(fn + ".shx")
    zf.write(fn + ".dbf")
    zf.write(fn + ".prj")
    zf.close()

    sys.stdout.write("Content-type: application/octet-stream\n")
    sys.stdout.write(
        "Content-Disposition: attachment; filename=%s.zip\n\n" % (fn, ))
    sys.stdout.write(file(fn + ".zip", 'r').read())

    for suffix in ['zip', 'shp', 'shx', 'dbf', 'prj']:
        os.remove("%s.%s" % (fn, suffix))


if __name__ == '__main__':
    main()
