import ee
import os
import sys
import json
import logging
import requests
import warnings
import subprocess
import numpy as np
from io import BytesIO
import matplotlib as mpl
from matplotlib import cm, colors
from collections.abc import Iterable


try:

    from PIL import Image
    import cartopy.crs as ccrs
    from cartopy.mpl.geoaxes import GeoAxes, GeoAxesSubplot
    from cartopy.mpl.gridliner import LATITUDE_FORMATTER, LONGITUDE_FORMATTER

except ImportError:

    print(
        "cartopy is not installed. Please see https://scitools.org.uk/cartopy/docs/latest/installing.html#installing for instructions on how to install cartopy.\n"
    )
    print(
        "The easiest way to install cartopy is using conda: conda install -c conda-forge cartopy"
    )


def check_dependencies():
    """Helper function to check dependencies used for cartoee
    Dependencies not included in main geemap are: cartopy, PIL, and scipys

    raises:
        Exception: when conda is not found in path
        Exception: when auto install fails to install/import packages
    """

    import importlib

    # check if conda in in path and available to use
    is_conda = os.path.exists(os.path.join(sys.prefix, "conda-meta"))

    # raise error if conda not found
    if not is_conda:
        raise Exception(
            "Auto installation requires `conda`. Please install conda using the following instructions before use: https://docs.conda.io/projects/conda/en/latest/user-guide/install/"
        )

    # list of dependencies to check, ordered in decreasing complexity
    # i.e. cartopy install should install PIL
    dependencies = ["cartopy", "pillow", "scipy"]

    # loop through dependency list and check if we can import module
    # if not try to install
    # install fail will be silent to continue through others if there is a failure
    # correct install will be checked later
    for dependency in dependencies:
        try:
            # see if we can import
            mod = importlib.import_module(dependency)
        except ImportError:
            # change the dependency name if it is PIL
            # import vs install names are different for PIL...
            # dependency = dependency if dependency is not "PIL" else "pillow"

            # print info if not installed
            logging.info(
                f"The {dependency} package is not installed. Trying install..."
            )

            logging.info(f"Installing {dependency} ...")

            # run the command
            cmd = f"conda install -c conda-forge {dependency} -y"
            proc = subprocess.Popen(
                cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
            )
            # send command
            out, err = proc.communicate()

            logging.info(out.decode())

    # second pass through dependencies to check if everything was installed correctly
    failed = []

    for dependency in dependencies:
        try:
            mod = importlib.import_module(dependency)
        except ImportError:
            # append failed imports
            failed.append(dependency)

    # check if there were any failed imports after trying install
    if len(failed) > 0:
        failed_str = ",".join(failed)
        raise Exception(
            f"Auto installation failed...the following dependencies were not installed '{failed_str}'"
        )
    else:
        logging.info("All dependencies are successfully imported/installed!")

    return


# check_dependencies()


def get_map(img_obj, proj=None, **kwargs):
    """
    Wrapper function to create a new cartopy plot with project and adds Earth
    Engine image results
    Args:
        img_obj (ee.Image): Earth Engine image result to plot
        proj (cartopy.crs, optional): Cartopy projection that determines the projection of the resulting plot. By default uses an equirectangular projection, PlateCarree
        **kwargs: remaining keyword arguments are passed to addLayer()
    Returns:
        ax (cartopy.mpl.geoaxes.GeoAxesSubplot): cartopy GeoAxesSubplot object with Earth Engine results displayed
    """
    if proj is None:
        proj = ccrs.PlateCarree()

    ax = mpl.pyplot.axes(projection=proj)
    add_layer(ax, img_obj, **kwargs)

    return ax


def add_layer(ax, img_obj, dims=1000, region=None, cmap=None, vis_params=None):
    """Add an Earth Engine image to a cartopy plot.

    args:
        img_obj (ee.image.Image): Earth Engine image result to plot.
        ax (cartopy.mpl.geoaxes.GeoAxesSubplot | cartopy.mpl.geoaxes.GeoAxes): required cartopy GeoAxesSubplot object to add image overlay to
        dims (list | tuple | int, optional): dimensions to request earth engine result as [WIDTH,HEIGHT]. If only one number is passed, it is used as the maximum, and the other dimension is computed by proportional scaling. Default None and infers dimesions
        region (list | tuple, optional): geospatial region of the image to render in format [E,S,W,N]. By default, the whole image
        cmap (str, optional): string specifying matplotlib colormap to colorize image. If cmap is specified visParams cannot contain 'palette' key
        vis_params (dict, optional): visualization parameters as a dictionary. See https://developers.google.com/earth-engine/image_visualization for options

    returns:
        ax (cartopy.mpl.geoaxes.GeoAxesSubplot): cartopy GeoAxesSubplot object with Earth Engine results displayed

    raises:
        ValueError: If `dims` is not of type list, tuple, or int
        ValueError: If `imgObj` is not of type ee.image.Image
        ValueError: If `ax` if not of type cartopy.mpl.geoaxes.GeoAxesSubplot '
    """

    if type(img_obj) is not ee.image.Image:
        raise ValueError("provided `img_obj` is not of type ee.Image")

    if region is not None:
        map_region = ee.Geometry.Rectangle(region).getInfo()["coordinates"]
        view_extent = (region[0], region[2], region[1], region[3])
    else:
        map_region = img_obj.geometry(100).bounds().getInfo()["coordinates"]
        # get the image bounds
        x, y = list(zip(*map_region[0]))
        view_extent = [min(x), max(x), min(y), max(y)]

    if type(dims) not in [list, tuple, int]:
        raise ValueError("provided dims not of type list, tuple, or int")

    if type(ax) not in [GeoAxes, GeoAxesSubplot]:
        raise ValueError(
            "provided axes not of type cartopy.mpl.geoaxes.GeoAxes "
            "or cartopy.mpl.geoaxes.GeoAxesSubplot"
        )

    args = {"format": "png", "crs": "EPSG:4326"}
    if region:
        args["region"] = map_region
    if dims:
        args["dimensions"] = dims

    if vis_params:
        keys = list(vis_params.keys())
        if cmap and ("palette" in keys):
            raise KeyError(
                "cannot provide `palette` in vis_params if `cmap` is specified"
            )
        elif cmap:
            args["palette"] = ",".join(build_palette(cmap))
        else:
            pass

        args = {**args, **vis_params}

    url = img_obj.getThumbUrl(args)
    response = requests.get(url)
    if response.status_code != 200:
        error = eval(response.content)["error"]
        raise requests.exceptions.HTTPError(f"{error}")

    image = np.array(Image.open(BytesIO(response.content)))

    if image.shape[-1] == 2:
        image = np.concatenate(
            [np.repeat(image[:, :, 0:1], 3, axis=2), image[:, :, -1:]], axis=2
        )

    ax.imshow(
        np.squeeze(image),
        extent=view_extent,
        origin="upper",
        transform=ccrs.PlateCarree(),
    )

    return


def build_palette(cmap, n=256):
    """Creates hex color code palette from a matplotlib colormap

    args:
        cmap (str): string specifying matplotlib colormap to colorize image. If cmap is specified visParams cannot contain 'palette' key
        n (int, optional): Number of hex color codes to create from colormap. Default is 256

    returns:
        palette (list[str]): list of hex color codes from matplotlib colormap for n intervals
    """

    colormap = cm.get_cmap(cmap, n)
    vals = np.linspace(0, 1, n)
    palette = list(map(lambda x: colors.rgb2hex(colormap(x)[:3]), vals))

    return palette


def add_colorbar(
    ax, vis_params, loc=None, cmap="gray", discrete=False, label=None, **kwargs
):
    """
    Add a colorbar tp the map based on visualization parameters provided
    args:
        ax (cartopy.mpl.geoaxes.GeoAxesSubplot | cartopy.mpl.geoaxes.GeoAxes): required cartopy GeoAxesSubplot object to add image overlay to
        loc (str, optional): string specifying the position
        vis_params (dict, optional): visualization parameters as a dictionary. See https://developers.google.com/earth-engine/image_visualization for options
        **kwargs: remaining keyword arguments are passed to colorbar()

    raises:
        Warning: If 'discrete' is true when "palette" key is not in visParams
        ValueError: If `ax` is not of type cartopy.mpl.geoaxes.GeoAxesSubplot
        ValueError: If 'cmap' or "palette" key in visParams is not provided
        ValueError: If "min" in visParams is not of type scalar
        ValueError: If "max" in visParams is not of type scalar
        ValueError: If 'loc' or 'cax' keywords are not provided
        ValueError: If 'loc' is not of type str or does not equal available options
    """

    if type(ax) not in [GeoAxes, GeoAxesSubplot]:
        raise ValueError(
            "provided axes not of type cartopy.mpl.geoaxes.GeoAxes "
            "or cartopy.mpl.geoaxes.GeoAxesSubplot"
        )

    if loc:
        if (type(loc) == str) and (loc in ["left", "right", "bottom", "top"]):
            posOpts = {
                "left": [0.01, 0.25, 0.02, 0.5],
                "right": [0.88, 0.25, 0.02, 0.5],
                "bottom": [0.25, 0.15, 0.5, 0.02],
                "top": [0.25, 0.88, 0.5, 0.02],
            }

            cax = ax.figure.add_axes(posOpts[loc])

            if loc == "left":
                mpl.pyplot.subplots_adjust(left=0.18)
            elif loc == "right":
                mpl.pyplot.subplots_adjust(right=0.85)
            else:
                pass

        else:
            raise ValueError(
                'provided loc not of type str. options are "left", '
                '"top", "right", or "bottom"'
            )

    elif "cax" in kwargs:
        cax = kwargs["cax"]
        kwargs = {key: kwargs[key] for key in kwargs.keys() if key != "cax"}

    else:
        raise ValueError("loc or cax keywords must be specified")

    vis_keys = list(vis_params.keys())
    if vis_params:
        if "min" in vis_params:
            vmin = vis_params["min"]
            if type(vmin) not in (int, float):
                raise ValueError("provided min value not of scalar type")
        else:
            vmin = 0

        if "max" in vis_params:
            vmax = vis_params["max"]
            if type(vmax) not in (int, float):
                raise ValueError("provided max value not of scalar type")
        else:
            vmax = 1

        if "opacity" in vis_params:
            alpha = vis_params["opacity"]
            if type(alpha) not in (int, float):
                raise ValueError("provided opacity value of not type scalar")
        elif "alpha" in kwargs:
            alpha = kwargs["alpha"]
        else:
            alpha = 1

        if cmap is not None:
            if discrete:
                warnings.warn(
                    'discrete keyword used when "palette" key is '
                    "supplied with visParams, creating a continuous "
                    "colorbar..."
                )

            cmap = mpl.pyplot.get_cmap(cmap)
            norm = mpl.colors.Normalize(vmin=vmin, vmax=vmax)

        if "palette" in vis_keys:
            hexcodes = vis_params["palette"].split(",")
            hexcodes = [i if i[0] == "#" else "#" + i for i in hexcodes]

            if discrete:
                cmap = mpl.colors.ListedColormap(hexcodes)
                vals = np.linspace(vmin, vmax, cmap.N + 1)
                norm = mpl.colors.BoundaryNorm(vals, cmap.N)

            else:
                cmap = mpl.colors.LinearSegmentedColormap.from_list(
                    "custom", hexcodes, N=256
                )
                norm = mpl.colors.Normalize(vmin=vmin, vmax=vmax)

            cmap = cmap

        elif cmap is not None:
            if discrete:
                warnings.warn(
                    'discrete keyword used when "palette" key is '
                    "supplied with visParams, creating a continuous "
                    "colorbar..."
                )

            cmap = mpl.pyplot.get_cmap(cmap)
            norm = mpl.colors.Normalize(vmin=vmin, vmax=vmax)

        else:
            raise ValueError(
                'cmap keyword or "palette" key in visParams must be provided'
            )

    cb = mpl.colorbar.ColorbarBase(cax, norm=norm, alpha=alpha, cmap=cmap, **kwargs)

    if "bands" in vis_keys:
        cb.set_label(vis_params["bands"])
    elif label is not None:
        cb.set_label(label)

    return


def _buffer_box(bbox, interval):
    """Helper function to buffer a bounding box to the nearest multiple of interval

    args:
        bbox (list[float]): list of float values specifying coordinates, expects order to be [W,E,S,N]
        interval (float): float specifying multiple at which to buffer coordianates to

    returns:
        extent (tuple[float]): returns tuple of buffered coordinates rounded to interval in order of [W,E,S,N]
    """

    if bbox[0] % interval != 0:
        xmin = bbox[0] - (bbox[0] % interval)
    else:
        xmin = bbox[0]

    if bbox[1] % interval != 0:
        xmax = bbox[1] + (interval - (bbox[1] % interval))
    else:
        xmax = bbox[1]

    if bbox[2] % interval != 0:
        ymin = bbox[2] - (bbox[2] % interval)
    else:
        ymin = bbox[2]

    if bbox[3] % interval != 0:
        ymax = bbox[3] + (interval - (bbox[3] % interval))
    else:
        ymax = bbox[3]

    return (xmin, xmax, ymin, ymax)


def bbox_to_extent(bbox):
    """Helper function to reorder a list of coordinates from [W,S,E,N] to [W,E,S,N]

    args:
        bbox (list[float]): list (or tuple) or coordinates in the order of [W,S,E,N]

    returns:
        extent (tuple[float]): tuple of coordinates in the order of [W,E,S,N]
    """
    return (bbox[0], bbox[2], bbox[1], bbox[3])


def add_gridlines(
    ax,
    interval=None,
    n_ticks=None,
    xs=None,
    ys=None,
    buffer_out=True,
    xtick_rotation="horizontal",
    ytick_rotation="horizontal",
    **kwargs,
):
    """Helper function to add gridlines and format ticks to map

    args:
        ax (cartopy.mpl.geoaxes.GeoAxesSubplot | cartopy.mpl.geoaxes.GeoAxes): required cartopy GeoAxesSubplot object to add the gridlines to
        interval (float | list[float], optional): float specifying an interval at which to create gridlines, units are decimal degrees. lists will be interpreted a [x_interval, y_interval]. default = None
        n_ticks (int | list[int], optional): integer specifying number gridlines to create within map extent. lists will be interpreted a [nx, ny]. default = None
        xs (list, optional): list of x coordinates to create gridlines. default = None
        ys (list, optional): list of y coordinates to create gridlines. default = None
        buffer_out (boolean, optional): boolean option to buffer out the extent to insure coordinates created cover map extent. default=true
        xtick_rotation (str | float, optional):
        ytick_rotation (str | float, optional):
        **kwargs: remaining keyword arguments are passed to gridlines()

    raises:
        ValueError: if all interval, n_ticks, or (xs,ys) are set to None

    """

    view_extent = ax.get_extent()
    extent = view_extent

    if xs is not None:
        xmain = xs

    elif interval is not None:
        if isinstance(interval, Iterable):
            xspace = interval[0]
        else:
            xspace = interval

        if buffer_out:
            extent = _buffer_box(extent, xspace)

        xmain = np.arange(extent[0], extent[1] + xspace, xspace)

    elif n_ticks is not None:
        if isinstance(n_ticks, Iterable):
            n_x = n_ticks[0]
        else:
            n_x = n_ticks

        xmain = np.linspace(extent[0], extent[1], n_x)
    else:
        raise ValueError(
            "one of variables interval, n_ticks, or xs must be defined. If you would like default gridlines, please use `ax.gridlines()`"
        )

    if ys is not None:
        ymain = ys

    elif interval is not None:
        if isinstance(interval, Iterable):
            yspace = interval[1]
        else:
            yspace = interval

        if buffer_out:
            extent = _buffer_box(extent, yspace)

        ymain = np.arange(extent[2], extent[3] + yspace, yspace)

    elif n_ticks is not None:
        if isinstance(n_ticks, Iterable):
            n_y = n_ticks[1]
        else:
            n_y = n_ticks

        ymain = np.linspace(extent[2], extent[3], n_y)

    else:
        raise ValueError(
            "one of variables interval, n_ticks, or ys must be defined. If you would like default gridlines, please use `ax.gridlines()`"
        )

    gl = ax.gridlines(xlocs=xmain, ylocs=ymain, **kwargs)

    xin = xmain[(xmain >= view_extent[0]) & (xmain <= view_extent[1])]
    yin = ymain[(ymain >= view_extent[2]) & (ymain <= view_extent[3])]

    # set tick labels
    ax.set_xticks(xin, crs=ccrs.PlateCarree())
    ax.set_yticks(yin, crs=ccrs.PlateCarree())

    ax.set_xticklabels(xin, rotation=xtick_rotation, ha="center")
    ax.set_yticklabels(yin, rotation=ytick_rotation, va="center")

    ax.xaxis.set_major_formatter(LONGITUDE_FORMATTER)
    ax.yaxis.set_major_formatter(LATITUDE_FORMATTER)

    return


def pad_view(ax, factor=0.05):
    """Function to pad area around the view extent of a map, used for visual appeal

    args:
        ax (cartopy.mpl.geoaxes.GeoAxesSubplot | cartopy.mpl.geoaxes.GeoAxes): required cartopy GeoAxesSubplot object to pad view extent
        factor (float | list[float], optional): factor to pad view extent accepts float [0-1] of a list of floats which will be interpreted at [xfactor, yfactor]

    """

    view_extent = ax.get_extent()

    if isinstance(factor, Iterable):
        xfactor, yfactor = factor
    else:
        xfactor, yfactor = factor, factor

    x_diff = view_extent[1] - view_extent[0]
    y_diff = view_extent[3] - view_extent[2]

    xmin = view_extent[0] - (x_diff * xfactor)
    xmax = view_extent[1] + (x_diff * xfactor)
    ymin = view_extent[2] - (y_diff * yfactor)
    ymax = view_extent[3] + (y_diff * yfactor)

    ax.set_ylim(ymin, ymax)
    ax.set_xlim(xmin, xmax)

    return


def add_north_arrow(
    ax,
    text="N",
    xy=(0.1, 0.1),
    arrow_length=0.1,
    text_color="black",
    arrow_color="black",
    fontsize=20,
    width=5,
    headwidth=15,
    ha="center",
    va="center",
):
    """Add a north arrow to the map.

    Args:
        ax (cartopy.mpl.geoaxes.GeoAxesSubplot | cartopy.mpl.geoaxes.GeoAxes): required cartopy GeoAxesSubplot object.
        text (str, optional): Text for north arrow. Defaults to "N".
        xy (tuple, optional): Location of the north arrow. Each number representing the percentage length of the map from the lower-left cornor. Defaults to (0.1, 0.1).
        arrow_length (float, optional): Length of the north arrow. Defaults to 0.1 (10% length of the map).
        text_color (str, optional): Text color. Defaults to "black".
        arrow_color (str, optional): North arrow color. Defaults to "black".
        fontsize (int, optional): Text font size. Defaults to 20.
        width (int, optional): Width of the north arrow. Defaults to 5.
        headwidth (int, optional): head width of the north arrow. Defaults to 15.
        ha (str, optional): Horizontal alignment. Defaults to "center".
        va (str, optional): Vertical alignment. Defaults to "center".
    """
    ax.annotate(
        text,
        xy=xy,
        xytext=(xy[0], xy[1] - arrow_length),
        color=text_color,
        arrowprops=dict(facecolor=arrow_color, width=width, headwidth=headwidth),
        ha=ha,
        va=va,
        fontsize=fontsize,
        xycoords=ax.transAxes,
    )

    return


def convert_SI(val, unit_in, unit_out):
    """Unit converter.

    Args:
        val (float): The value to convert.
        unit_in (str): The input unit.
        unit_out (str): The output unit.

    Returns:
        float: The value after unit conversion.
    """
    SI = {
        "cm": 0.01,
        "m": 1.0,
        "km": 1000.0,
        "inch": 0.0254,
        "foot": 0.3048,
        "mile": 1609.34,
    }
    return val * SI[unit_in] / SI[unit_out]


def add_scale_bar(
    ax,
    length=None,
    xy=(0.5, 0.05),
    linewidth=3,
    fontsize=20,
    color="black",
    unit="km",
    ha="center",
    va="bottom",
):
    """Add a scale bar to the map. Reference: https://stackoverflow.com/a/50674451/2676166

    Args:
        ax (cartopy.mpl.geoaxes.GeoAxesSubplot | cartopy.mpl.geoaxes.GeoAxes): required cartopy GeoAxesSubplot object.
        length ([type], optional): Length of the scale car. Defaults to None.
        xy (tuple, optional): Location of the north arrow. Each number representing the percentage length of the map from the lower-left cornor. Defaults to (0.1, 0.1).
        linewidth (int, optional): Line width of the scale bar. Defaults to 3.
        fontsize (int, optional): Text font size. Defaults to 20.
        color (str, optional): Color for the scale bar. Defaults to "black".
        unit (str, optional): Length unit for the scale bar. Defaults to "km".
        ha (str, optional): Horizontal alignment. Defaults to "center".
        va (str, optional): Vertical alignment. Defaults to "bottom".

    """

    allow_units = ["cm", "m", "km", "inch", "foot", "mile"]
    if unit not in allow_units:
        print(
            "The unit must be one of the following: {}".format(", ".join(allow_units))
        )
        return

    num = length

    # Get the limits of the axis in lat long
    llx0, llx1, lly0, lly1 = ax.get_extent(ccrs.PlateCarree())
    # Make tmc horizontally centred on the middle of the map,
    # vertically at scale bar location
    sbllx = (llx1 + llx0) / 2
    sblly = lly0 + (lly1 - lly0) * xy[1]
    tmc = ccrs.TransverseMercator(sbllx, sblly, approx=True)
    # Get the extent of the plotted area in coordinates in metres
    x0, x1, y0, y1 = ax.get_extent(tmc)
    # Turn the specified scalebar location into coordinates in metres
    sbx = x0 + (x1 - x0) * xy[0]
    sby = y0 + (y1 - y0) * xy[1]

    # Calculate a scale bar length if none has been given
    # (Theres probably a more pythonic way of rounding the number but this works)
    if not length:
        length = (x1 - x0) / 5000  # in km
        ndim = int(np.floor(np.log10(length)))  # number of digits in number
        length = round(length, -ndim)  # round to 1sf
        # Returns numbers starting with the list
        def scale_number(x):
            if str(x)[0] in ["1", "2", "5"]:
                return int(x)
            else:
                return scale_number(x - 10 ** ndim)

        length = scale_number(length)
        num = length
    else:
        length = convert_SI(length, unit, "km")

    # Generate the x coordinate for the ends of the scalebar
    bar_xs = [sbx - length * 500, sbx + length * 500]
    # Plot the scalebar
    ax.plot(bar_xs, [sby, sby], transform=tmc, color=color, linewidth=linewidth)
    # Plot the scalebar label
    ax.text(
        sbx,
        sby,
        str(num) + " " + unit,
        transform=tmc,
        horizontalalignment=ha,
        verticalalignment=va,
        color=color,
        fontsize=fontsize,
    )

    return


def get_image_collection_gif(
    ee_ic,
    out_dir,
    out_gif,
    vis_params,
    region,
    cmap=None,
    proj=None,
    fps=10,
    mp4=False,
    grid_interval=None,
    plot_title="",
    date_format="YYYY-MM-dd",
    fig_size=(10, 10),
    dpi_plot=100,
    file_format="png",
    north_arrow_dict={},
    scale_bar_dict={},
    verbose=True,
):
    """Download all the images in an image collection and use them to generate a gif/video.
    Args:
        ee_ic (object): ee.ImageCollection
        out_dir (str): The output directory of images and video.
        out_gif (str): The name of the gif file.
        vis_params (dict): Visualization parameters as a dictionary.
        region (list | tuple): Geospatial region of the image to render in format [E,S,W,N].
        fps (int, optional): Video frames per second. Defaults to 10.
        mp4 (bool, optional): Whether to create mp4 video.
        grid_interval (float | tuple[float]): Float specifying an interval at which to create gridlines, units are decimal degrees. lists will be interpreted a (x_interval, y_interval), such as (0.1, 0.1). Defaults to None.
        plot_title (str): Plot title. Defaults to "".
        date_format (str, optional): A pattern, as described at http://joda-time.sourceforge.net/apidocs/org/joda/time/format/DateTimeFormat.html. Defaults to "YYYY-MM-dd".
        fig_size (tuple, optional): Size of the figure.
        dpi_plot (int, optional): The resolution in dots per inch of the plot.
        file_format (str, optional): Either 'png' or 'jpg'.
        north_arrow_dict (dict, optional). Parameters for the north arrow. See https://geemap.org/cartoee/#geemap.cartoee.add_north_arrow. Defaults to {}.
        scale_bar_dict (dict, optional): Parameters for the scale bar. See https://geemap.org/cartoee/#geemap.cartoee.add_scale_bar. Defaults. to {}.
        verbose (bool, optional): Whether or not to print text when the program is running. Defaults to True.
    """

    from .geemap import png_to_gif

    import matplotlib.pyplot as plt

    out_dir = os.path.abspath(out_dir)
    if not os.path.exists(out_dir):
        os.makedirs(out_dir)

    out_gif = os.path.join(out_dir, out_gif)

    count = int(ee_ic.size().getInfo())
    names = ee_ic.aggregate_array("system:index").getInfo()
    images = ee_ic.toList(count)

    dates = ee_ic.aggregate_array("system:time_start")
    dates = dates.map(lambda d: ee.Date(d).format(date_format)).getInfo()

    # list of file name
    img_list = []

    for i, date in enumerate(dates):
        image = ee.Image(images.get(i))
        name = str(names[i])
        name = name + "." + file_format
        out_img = os.path.join(out_dir, name)
        img_list.append(out_img)

        if verbose:
            print(f"Downloading {i+1}/{count}: {name} ...")

        # Size plot
        plt.figure(figsize=fig_size)

        # Plot image
        ax = get_map(image, region=region, vis_params=vis_params, cmap=cmap, proj=proj)

        # Add grid
        if grid_interval is not None:
            add_gridlines(ax, interval=grid_interval, linestyle=":")

        # Add title
        if len(plot_title) > 0:
            ax.set_title(label=plot_title + " " + date + "\n", fontsize=15)

        # Add scale bar
        if len(scale_bar_dict) > 0:
            add_scale_bar(ax, **scale_bar_dict)
        # Add north arrow
        if len(north_arrow_dict) > 0:
            add_north_arrow(ax, **north_arrow_dict)

        # Save plot
        plt.savefig(fname=out_img, dpi=dpi_plot)

        plt.clf()
        plt.close()

    out_gif = os.path.abspath(out_gif)
    png_to_gif(out_dir, out_gif, fps)
    if verbose:
        print(f"GIF saved to {out_gif}")

    if mp4:

        video_filename = out_gif.replace(".gif", ".mp4")

        try:
            import cv2
        except ImportError:
            print("Installing opencv-python ...")
            subprocess.check_call(["python", "-m", "pip", "install", "opencv-python"])
            import cv2

        # Video file name
        output_video_file_name = os.path.join(out_dir, video_filename)

        frame = cv2.imread(img_list[0])
        height, width, _ = frame.shape
        frame_size = (width, height)
        fps_video = fps

        # Make mp4
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")

        # Function
        def convert_frames_to_video(
            input_list, output_video_file_name, fps_video, frame_size
        ):

            """Convert frames to video

            Args:

                input_list (list): Downloaded Image Name List.
                output_video_file_name (str): The name of the video file in the image directory.
                fps_video (int): Video frames per second.
                frame_size (tuple): Frame size.
            """
            out = cv2.VideoWriter(output_video_file_name, fourcc, fps_video, frame_size)
            num_frames = len(input_list)

            for i in range(num_frames):
                img_path = input_list[i]
                img = cv2.imread(img_path)
                out.write(img)

            out.release()
            cv2.destroyAllWindows()

        # Use function
        convert_frames_to_video(
            input_list=img_list,
            output_video_file_name=output_video_file_name,
            fps_video=fps_video,
            frame_size=frame_size,
        )

        if verbose:
            print(f"MP4 saved to {output_video_file_name}")
