import psycopg2
import datetime
from pyiem.network import Table as NetworkTable
from pandas.io.sql import read_sql
from scipy import stats


def get_description():
    """ Return a dict describing how to call this plotter """
    d = dict()
    d['data'] = True
    d['cache'] = 86400
    d['description'] = """This plot displays some metrics about the start
    date and duration of the spring season.  The definition of the season
    being the period between the coldest 91 day stretch and subsequent
    warmest 91 day stretch.  91 days being approximately 1/4 of the year,
    assuming the four seasons are to be equal duration.  Of course, this is
    arbitrary, but interesting to look at!
    """
    d['arguments'] = [
        dict(type='station', name='station', default='IA0200',
             label='Select Station:'),
        dict(type='year', name='year',
             default=(datetime.date.today().year - 2),
             label='Select Start Year (3 years plotted) for Top Panel:'),
    ]
    return d


def plotter(fdict):
    """ Go """
    import matplotlib
    matplotlib.use('agg')
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    station = fdict.get('station', 'IA0200')
    network = fdict.get('network', 'IACLIMATE')
    year = int(fdict.get('year', (datetime.date.today().year - 2)))
    nt = NetworkTable(network)
    table = "alldata_%s" % (station[:2],)

    pgconn = psycopg2.connect(database='coop', host='iemdb', user='nobody')

    # Have to do a redundant query to get the running values
    obs = read_sql("""
    WITH trail as (
        SELECT day, year,
        avg((high+low)/2.) OVER (ORDER by day ASC ROWS 91 PRECEDING) as avgt
        from """ + table + """ WHERE station = %s)

    SELECT day, avgt from trail WHERE year between %s and %s ORDER by day ASC
    """, pgconn, params=(station, year, year + 2), index_col='day')

    df = read_sql("""
    WITH trail as (
        SELECT day, year,
        avg((high+low)/2.) OVER (ORDER by day ASC ROWS 91 PRECEDING) as avgt
        from """ + table + """ WHERE station = %s),
    extremes as (
        SELECT day, year, avgt,
        rank() OVER (PARTITION by year ORDER by avgt ASC) as minrank,
        rank() OVER (PARTITION by year ORDER by avgt DESC) as maxrank
        from trail),
    yearmax as (
        SELECT year, min(day) as summer_end, min(avgt) as summer
        from extremes where maxrank = 1 GROUP by year),
    yearmin as (
        SELECT year, min(day) as winter_end, min(avgt) as winter
        from extremes where minrank = 1 GROUP by year)

    SELECT x.year, winter_end, winter, summer_end, summer,
    extract(doy from winter_end)::int as winter_end_doy,
    extract(doy from summer_end)::int as summer_end_doy
    from yearmax x JOIN yearmin n on (x.year = n.year) ORDER by x.year ASC
    """, pgconn, params=(station, ), index_col='year')
    # Throw out spring of the first year
    for col in ['winter', 'winter_end_doy', 'winter_end']:
        df.at[df.index.min(), col] = None

    # Need to cull current year
    if datetime.date.today().month < 8:
        for col in ['summer', 'summer_end_doy', 'summer_end']:
            df.at[datetime.date.today().year, col] = None
    if datetime.date.today().month < 2:
        for col in ['winter', 'winter_end_doy', 'winter_end']:
            df.at[datetime.date.today().year, col] = None
    df['spring_length'] = df['summer_end_doy'] - 91 - df['winter_end_doy']

    (fig, ax) = plt.subplots(3, 1, figsize=(8, 9))

    ax[0].plot(obs.index.values, obs['avgt'].values)
    ax[0].set_title(("%s-%s [%s] %s\n91 Day Average Temperatures"
                     ) % (nt.sts[station]['archive_begin'].year,
                          year + 3, station, nt.sts[station]['name']))
    ax[0].set_ylabel("Trailing 91 Day Avg T $^{\circ}$F")
    ax[0].xaxis.set_major_formatter(mdates.DateFormatter('%b\n%Y'))
    ax[0].grid(True)

    # Label the maxes and mins
    for yr in range(year, year+3):
        if yr not in df.index:
            continue
        date = df.at[yr, 'winter_end']
        val = df.at[yr, 'winter']
        if date is not None:
            ax[0].text(date, val - 5,
                       "%s %.1f$^\circ$F" % (date.strftime("%-d %b"),
                                             val), ha='center')
        date = df.at[yr, 'summer_end']
        val = df.at[yr, 'summer']
        if date is not None:
            ax[0].text(date, val + 2,
                       "%s %.1f$^\circ$F" % (date.strftime("%-d %b"),
                                             val), ha='center')

    slp, intercept, r, _, _ = stats.linregress(df.index.values,
                                               df['winter_end_doy'].values)
    ax[1].scatter(df.index.values, df['winter_end_doy'].values)
    ax[1].grid(True)
    # Do labelling
    yticks = []
    yticklabels = []
    for doy in range(int(df['winter_end_doy'].min()),
                     int(df['winter_end_doy'].max())):
        date = datetime.date(2000, 1, 1) + datetime.timedelta(days=(doy - 1))
        if date.day in [1, 15]:
            yticks.append(doy)
            yticklabels.append(date.strftime("%-d %b"))
    ax[1].set_yticks(yticks)
    ax[1].set_yticklabels(yticklabels)
    ax[1].set_ylabel("Date of Minimum (Spring Start)")
    ax[1].set_xlim(df.index.min() - 1, df.index.max() + 1)
    avgv = df['winter_end_doy'].mean()
    ax[1].axhline(avgv, color='r')
    ax[1].plot(df.index.values, intercept + (df.index.values * slp))
    d = (datetime.date(2000, 1, 1) +
         datetime.timedelta(days=int(avgv))).strftime("%-d %b")
    ax[1].text(0.02, 0.02,
               r"$\frac{\Delta days}{decade} = %.2f,R^2=%.2f, avg = %s$" % (
                    slp * 10.0, r ** 2, d), va='bottom',
               transform=ax[1].transAxes)
    ax[1].set_ylim(bottom=(ax[1].get_ylim()[0] - 10))

    slp, intercept, r, _, _ = stats.linregress(df.index.values,
                                               df['spring_length'])
    ax[2].scatter(df.index.values, df['spring_length'])
    ax[2].set_xlim(df.index.min() - 1, df.index.max() + 1)
    ax[2].set_ylabel("Length of 'Spring' [days]")
    ax[2].grid(True)
    avgv = df['spring_length'].mean()
    ax[2].axhline(avgv, color='r')
    ax[2].plot(df.index.values, intercept + (df.index.values * slp))
    ax[2].text(0.02, 0.02,
               r"$\frac{\Delta days}{decade} = %.2f,R^2=%.2f, avg = %.1fd$" % (
                slp * 10.0, r ** 2, avgv),
               va='bottom', transform=ax[2].transAxes)
    ax[2].set_ylim(bottom=(ax[2].get_ylim()[0] - 15))

    return fig, df


if __name__ == '__main__':
    plotter(dict())