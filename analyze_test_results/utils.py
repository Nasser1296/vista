import os
from tqdm import tqdm
import numpy as np
import utm
import rosbag
import pickle
import matplotlib.pyplot as plt


def read_rosbag(bag_path):
    bag = rosbag.Bag(bag_path)
    topic_info = bag.get_type_and_topic_info()[1]
    data = {k: [] for k in topic_info.keys()}
    # TODO: add progress bar
    for topic, msg, t in tqdm(bag.read_messages()):
        data[topic].append([t.to_sec(), msg])
    bag.close()

    return data


def fetch_gps(data, topic='/lexus/oxts/gps/fix'):
    gps= []
    for t, msg in data[topic]:
        x, y, _, _ = utm.from_latlon(msg.latitude, msg.longitude)
        gps.append([t, x, y])
    gps = np.array(gps)
    origin = gps[0,1:]
    return gps


def fetch_yaw(data, topic='/lexus/oxts/imu/data'):
    yaws = []
    for t, msg in data[topic]:
        q = msg.orientation
        yaw = np.arctan2(2.*(q.x*q.y + q.z*q.w) , (q.w**2 - q.z**2 - q.y*2 + q.x**2))
        yaws.append([t, yaw])
    yaws = np.array(yaws)
    return yaws


def fetch_intervention(data, topic='/lexus/ssc/module_states', 
                       filter_too_close=True, min_t_diff=10.):
    intervention = []
    for t, msg in data[topic]:
        if msg.info == 'Operator Override':
            too_close = (t - intervention[-1]) < min_t_diff if len(intervention) > 0 else False
            if filter_too_close and too_close:
                continue
            intervention.append(t)
    return intervention


def fetch_curvature(data, topic):
    curvatures = []
    for t, msg in data[topic]:
        try:
            curvatures.append([t, msg.data])
        except:
            curvatures.append([t, msg.curvature])
    curvatures = np.array(curvatures)
    return curvatures


def fetch_speed(data, topic='/lexus/pacmod/parsed_tx/vehicle_speed_rpt'):
    speed = []
    for t, msg in data[topic]:
        if msg.vehicle_speed_valid:
            speed.append([t, msg.vehicle_speed])
    speed = np.array(speed)
    return speed


def validate_path(path):
    valid_path = ['/'] if path.startswith('/') else []
    for v in path.split('/'):
        if v.startswith('$'):
            v = v[1:]
            assert v in os.environ, f'Remember to set ${v}'
            v = os.environ[v]
        valid_path.append(v)
    valid_path = os.path.join(*valid_path)
    valid_path = os.path.abspath(os.path.expanduser(valid_path))
    return valid_path


def visualize_gps(gps, intervention=None, figax=None):
    if figax is None:
        fig, ax = plt.subplots(1, 1)
    else:
        fig, ax = figax
    ax.set_title('GPS')
    ax.plot(gps[:,1], gps[:,2], c='b', zorder=1)

    if intervention is not None:
        intervention_xy = []
        for ts in intervention:
            idx = np.argmin(np.abs(gps[:,0] - ts))
            intervention_xy.append([gps[idx,1], gps[idx,2]])
        intervention_xy = np.array(intervention_xy)
        ax.scatter(intervention_xy[:,0],intervention_xy[:,1], s=4, c='r', marker='o', zorder=2)

        for i, xy in enumerate(intervention_xy):
            ax.annotate(f'{i+1}', xy)

    return [fig, ax]


def load_devens_road(path):
    with open(path, 'rb') as f:
        loop_paths = pickle.load(f, encoding='latin1')

    return loop_paths


def plot_devens_road(loop_paths, figax=None):
    if figax is None:
        fig, ax = plt.subplots(1, 1)
    else:
        fig, ax = figax

    for (loop, path) in loop_paths.items():
        ax.plot(path[:,0], path[:,1], linewidth=0.25, color='k')
        ax.axis('equal')
    
    return [fig, ax]


def visualize_curvature(curvature_command, curvature_feedback, figax=None, ts_origin=None):
    if figax is None:
        fig, ax = plt.subplots(1, 1)
    else:
        fig, ax = figax
    ax.set_title('Curvature')
    ts_origin = curvature_command[0,0] if ts_origin is None else ts_origin
    cf = curvature_feedback[:,0] - ts_origin # avoid changing reference
    cc = curvature_command[:,0] - ts_origin
    ax.plot(cf, curvature_feedback[:,1], label='Feedback', c='r', zorder=1)
    ax.plot(cc, curvature_command[:,1], label='Command', c='b', zorder=2)

    return [fig, ax]


def visualize_speed(speed, figax=None, ts_origin=None):
    if figax is None:
        fig, ax = plt.subplots(1, 1)
    else:
        fig, ax = figax
    ax.set_title('Speed')
    ts_origin = speed[0,0] if ts_origin is None else ts_origin
    sp = speed[:,0] - ts_origin
    ax.plot(sp, speed[:,1])

    return [fig, ax]


def split_to_segments(data):
    ts = data[:,0]
    ts_diff = ts[1:] - ts[:-1]
    gaps = np.where(ts_diff > 2.)[0] + 1
    gaps = np.insert(gaps, 0, 0)
    segments = []
    for i in range(len(gaps) - 1):
        segments.append(data[gaps[i]:gaps[i+1]])
    segments.append(data[gaps[i+1]:])
    return segments
