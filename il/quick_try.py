import os
import numpy as np
import argparse
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader
from torchvision import transforms

import vista
from vista.entities.sensors.camera_utils.ViewSynthesis import DepthModes
from vista.utils import logging

logging.setLevel(logging.ERROR)


class VistaDataset(Dataset):
    def __init__(self, trace_paths, transform, train=False):
        trace_config = dict(
            road_width=4,
            reset_mode='segment_start',
            master_sensor='front_center',
        )
        car_config = dict(
            length=5.,
            width=2.,
            wheel_base=2.78,
            steering_ratio=14.7,
        )
        camera_config = dict(
            # camera params
            name='front_center',
            rig_path='/home/tsunw/data/traces/20200424-133758_blue_prius_cambridge_rain/RIG.xml',
            size=(200, 320),
            # rendering params
            depth_mode=DepthModes.FIXED_PLANE,
            use_lighting=False,
            use_synthesizer=False, # NOTE: don't do view synthesis
        )
        self.world = vista.World(trace_paths, trace_config)
        self.agent = self.world.spawn_agent(car_config)
        self.camera = self.agent.spawn_camera(camera_config)
        self.world.reset()

        self.transform = transform
        self.train = train

    def __len__(self):
        return np.sum([tr.num_of_frames for tr in self.world.traces])

    def __getitem__(self, idx):
        if self.agent.done:
            self.world.reset()
        self.agent.step_dataset(step_dynamics=False)
        sensor_name = self.agent.sensors[0].name
        img = self.agent.observations[sensor_name]

        (i1, j1, i2, j2) = self.agent.sensors[0].camera_param.get_roi()
        img = img[i1:i2, j1:j2]

        img = self.transform(img)
        label = np.array([self.agent.human_curvature]).astype(np.float32)
        return img, label


class Net(nn.Module):
    def __init__(self):
        super(Net, self).__init__()
        self.extractor = nn.Sequential(
            nn.Conv2d(3, 24, 5, 2, 2),
            nn.BatchNorm2d(24),
            nn.ReLU(inplace=True),
            nn.Conv2d(24, 36, 5, 2, 2),
            nn.BatchNorm2d(36),
            nn.ReLU(inplace=True),
            nn.Conv2d(36, 48, 3, 2, 1),
            nn.BatchNorm2d(48),
            nn.ReLU(inplace=True),
            nn.Conv2d(48, 64, 3, 1, 1),
            nn.BatchNorm2d(64),
            nn.ReLU(inplace=True),
            nn.Conv2d(64, 64, 3, 1, 1),
            nn.BatchNorm2d(64),
            nn.ReLU(inplace=True)
        )
        self.policy = nn.Sequential(
            nn.Linear(1280, 64),
            nn.BatchNorm1d(64),
            nn.ReLU(inplace=True),
            nn.Linear(64, 32),
            nn.BatchNorm1d(32),
            nn.ReLU(inplace=True),
            nn.Linear(32, 1)
        )

    def forward(self, x):
        z = self.extractor(x)
        z = torch.mean(z, dim=2)
        z = z.flatten(start_dim=1, end_dim=2)
        out = self.policy(z)
        return out


def train(args, model, device, train_loader, criterion, optimizer, epoch):
    model.train()
    for batch_idx, (data, target) in enumerate(train_loader):
        data, target = data.to(device), target.to(device)
        optimizer.zero_grad()
        output = model(data)
        loss = criterion(output, target) / len(data)
        loss.backward()
        optimizer.step()
        if batch_idx % 10 == 0:
            curr_n_samples = batch_idx * len(data)
            total_samples = len(train_loader.dataset)
            progress = 100. * batch_idx / len(train_loader)
            print(f'Training epoch {epoch} [{curr_n_samples}/{total_samples} ({progress:.2f}%)]' \
                  + f'\t Loss: {loss.item():.6f}')


def test(model, device, test_loader, criterion):
    model.eval()
    test_loss = 0
    with torch.no_grad():
        for data, target in test_loader:
            data, target = data.to(device), target.to(device)
            output = model(data)
            test_loss += criterion(output, target).item()

    test_loss /= len(test_loader.dataset)
    print(f'Test set: Average Loss: {test_loss:.6f}')
    return test_loss


def main():
    # Parse arguments (NOTE: just a placeholder here; hardcoded argument for now)
    parser = argparse.ArgumentParser(description='Minimal example of Vista IL training')
    parser.add_argument('--seed', type=int, default=1, metavar='S',
                        help='random seed (default: 1)')
    parser.add_argument('--no-cuda', action='store_true', default=False,
                        help='disables CUDA training')
    args = parser.parse_args()

    torch.manual_seed(args.seed)
    use_cuda = not args.no_cuda and torch.cuda.is_available()
    device = torch.device('cuda' if use_cuda else 'cpu')

    # Define data loader
    train_transform = transforms.Compose([
        transforms.ToTensor(),
        transforms.ColorJitter(),
    ])
    train_trace_paths = ['/home/tsunw/data/traces/20210527-131252_lexus_devens_center_outerloop',
                         '/home/tsunw/data/traces/20210527-131709_lexus_devens_center_outerloop_reverse',
                         '/home/tsunw/data/traces/20210609-122400_lexus_devens_outerloop_reverse',
                         '/home/tsunw/data/traces/20210609-123703_lexus_devens_outerloop',
                         '/home/tsunw/data/traces/20210609-133320_lexus_devens_outerloop',
                         '/home/tsunw/data/traces/20210609-154525_lexus_devens_sideroad',
                         '/home/tsunw/data/traces/20210609-154745_lexus_devens_outerloop_reverse',
                         '/home/tsunw/data/traces/20210609-155238_lexus_devens_outerloop',
                         '/home/tsunw/data/traces/20210609-155752_lexus_devens_subroad',
                         '/home/tsunw/data/traces/20210609-175037_lexus_devens_outerloop_reverse',
                         '/home/tsunw/data/traces/20210609-175503_lexus_devens_outerloop']
    train_dataset = VistaDataset(train_trace_paths, train_transform, train=True)
    train_loader = DataLoader(train_dataset,
                              batch_size=64,
                              shuffle=True,
                              pin_memory=True,
                              num_workers=0, # NOTE: multi-process data loader make FFReader fail
                              drop_last=True)

    test_transform = transforms.Compose([
        transforms.ToTensor(),
    ])
    test_trace_paths = ['/home/tsunw/data/traces/20210613-171636_lexus_devens_outerloop',
                        '/home/tsunw/data/traces/20210613-172102_lexus_devens_outerloop_reverse',
                        '/home/tsunw/data/traces/20210613-194157_lexus_devens_subroad',
                        '/home/tsunw/data/traces/20210613-194324_lexus_devens_subroad_reverse']
    test_dataset = VistaDataset(test_trace_paths, test_transform, train=False)
    test_loader = DataLoader(test_dataset,
                             batch_size=64,
                             shuffle=False,
                             pin_memory=True,
                             num_workers=0,
                             drop_last=True)

    # Define model and optimizer
    model = Net().to(device)
    optimizer = optim.Adam(model.parameters(), lr=0.0002)
    criterion = nn.MSELoss(reduction='sum')

    # Run training
    all_test_loss = []
    try:
        for epoch in range(1, 100 + 1):
            train(args, model, device, train_loader, criterion, optimizer, epoch)
            test_loss = test(model, device, test_loader, criterion)
            all_test_loss.append(test_loss)
    except:
        import pdb; pdb.set_trace()


if __name__ == '__main__':
    main()
