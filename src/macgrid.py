"""
Staggered MAC grid implementation.
The skeleton was inspired by https://github.com/Wimacs/taichi_code/blob/master/flip%26apic%26pic/pic_flip.py, 
which implements a pic/flip fluid solver in 2D.
"""

from typing import Tuple
import taichi as ti
import numpy as np
import matplotlib.pyplot as plt
from enum import Enum


class CellType(Enum):
    AIR = 0
    SAND = 1
    SOLID = 2


@ti.data_oriented
class MacGrid:
    def __init__(
        self, grid_size: int, initial_sand_cells: Tuple, pic_fraction: float
    ) -> None:
        # grid parameters
        self.grid_size = grid_size
        self.pic_fraction = pic_fraction

        self.initial_sand_cells = initial_sand_cells

        # Cell centered grids
        self.cell_type = ti.field(
            ti.f32, shape=(self.grid_size, self.grid_size, self.grid_size)
        )
        self.pressure = ti.field(
            ti.f32, shape=(self.grid_size, self.grid_size, self.grid_size)
        )
        self.divergence = ti.field(
            ti.f32, shape=(self.grid_size, self.grid_size, self.grid_size)
        )

        # Sand specific fields
        # D
        self.strain_rate = ti.Matrix.field(
            3, 3, ti.f32, shape=(self.grid_size, self.grid_size, self.grid_size)
        )
        # sigma_f
        self.frictional_stress = ti.Matrix.field(
            3, 3, ti.f32, shape=(self.grid_size, self.grid_size, self.grid_size)
        )
        # sigma_f
        self.frictional_stress_divergence = ti.field(
            ti.f32, shape=(self.grid_size, self.grid_size, self.grid_size)
        )
        # sigma_rigid
        self.rigid_stress = ti.Matrix.field(
            3, 3, ti.f32, shape=(self.grid_size, self.grid_size, self.grid_size)
        )
        # Marks rigid sand cells
        self.cell_rigid = ti.field(
            ti.i32, shape=(self.grid_size, self.grid_size, self.grid_size)
        )

        # Edge centered grids
        self.v_x = ti.field(
            ti.f32, shape=(self.grid_size + 1, self.grid_size, self.grid_size)
        )
        self.v_y = ti.field(
            ti.f32, shape=(self.grid_size, self.grid_size + 1, self.grid_size)
        )
        self.v_z = ti.field(
            ti.f32, shape=(self.grid_size, self.grid_size, self.grid_size + 1)
        )

        self.v_x_saved = ti.field(
            ti.f32, shape=(self.grid_size + 1, self.grid_size, self.grid_size)
        )
        self.v_y_saved = ti.field(
            ti.f32, shape=(self.grid_size, self.grid_size + 1, self.grid_size)
        )
        self.v_z_saved = ti.field(
            ti.f32, shape=(self.grid_size, self.grid_size, self.grid_size + 1)
        )

        self.f_x = ti.field(
            ti.f32, shape=(self.grid_size + 1, self.grid_size, self.grid_size)
        )
        self.f_y = ti.field(
            ti.f32, shape=(self.grid_size, self.grid_size + 1, self.grid_size)
        )
        self.f_z = ti.field(
            ti.f32, shape=(self.grid_size, self.grid_size, self.grid_size + 1)
        )

        self.splat_x_weights = ti.field(
            ti.f32, shape=(self.grid_size + 1, self.grid_size, self.grid_size)
        )
        self.splat_y_weights = ti.field(
            ti.f32, shape=(self.grid_size, self.grid_size + 1, self.grid_size)
        )
        self.splat_z_weights = ti.field(
            ti.f32, shape=(self.grid_size, self.grid_size, self.grid_size + 1)
        )

        # particles
        self.particle_pos = ti.Vector.field(
            3,
            ti.f32,
            shape=(self.grid_size * 2, self.grid_size * 2, self.grid_size * 2),
        )
        # edge particles
        self.particle_edge_pos = ti.Vector.field(
            3,
            ti.f32,
            shape=(self.grid_size * 2, self.grid_size * 2, self.grid_size * 2),
        )
        self.particle_v = ti.Vector.field(
            3,
            ti.f32,
            shape=(self.grid_size * 2, self.grid_size * 2, self.grid_size * 2),
        )
        self.particle_active = ti.field(
            ti.i32, shape=(self.grid_size * 2, self.grid_size * 2, self.grid_size * 2)
        )

        # Initialize grids and particles
        self.reset_fields()

    @ti.kernel
    def reset_fields(self):
        # Cell centered grids
        self.clear_field(self.cell_type)
        self.clear_field(self.pressure)
        self.clear_field(self.divergence)

        # Edge centered grids
        self.clear_field(self.v_x)
        self.clear_field(self.v_y)
        self.clear_field(self.v_z)

        self.clear_field(self.f_x)
        self.clear_field(self.f_y)
        self.clear_field(self.f_z)

        # particles
        self.clear_field(self.particle_pos, [0.0, 0.0, 0.0])
        self.clear_field(self.particle_v, [0.0, 0.0, 0.0])
        self.clear_field(self.particle_active)

        # Initialize grids and particles
        self.init_cell_type()
        self.init_particles()

    # Initializes the cell types. The border of the grid is always solid
    # The cells with CellType SAND will have active particles
    @ti.func
    def init_cell_type(self):
        for i, j, k in self.cell_type:
            if (
                i == 0
                or j == 0
                or k == 0
                or i == self.grid_size - 1
                or j == self.grid_size - 1
                or k == self.grid_size - 1
            ):
                self.cell_type[i, j, k] = CellType.SOLID.value
            elif (
                self.initial_sand_cells[0][0] <= i <= self.initial_sand_cells[0][1]
                and self.initial_sand_cells[1][0] <= j <= self.initial_sand_cells[1][1]
                and self.initial_sand_cells[2][0] <= k <= self.initial_sand_cells[2][1]
            ):
                self.cell_type[i, j, k] = CellType.SAND.value
            else:
                self.cell_type[i, j, k] = CellType.AIR.value

    @ti.func
    def reset_cell_type(self):
        self.clear_field(self.cell_type)

        for i, j, k in self.cell_type:
            if (
                i == 0
                or j == 0
                or k == 0
                or i == self.grid_size - 1
                or j == self.grid_size - 1
                or k == self.grid_size - 1
            ):
                self.cell_type[i, j, k] = CellType.SOLID.value

    @ti.kernel
    def print_particles(self):
        for i, j, k in self.particle_pos:
            if self.particle_active[i, j, k] == 1:
                print("p_", i, "_", j, "_", k, "_: (", self.particle_pos[i, j, k], ")")

    # Initializes the particles to 8 particles per grid cell.
    # The positions are for grid cell i: i + 0.25 + rand and i + 0.75 + rand
    # In all dimensions and for a rand jitter between [-0.25, 0.25]
    # Only particles in SAND grid cells will be active
    @ti.func
    def init_particles(self):
        for i, j, k in self.particle_pos:
            grid_idx_i = i // 2
            grid_idx_j = j // 2
            grid_idx_k = k // 2
            even_i = i % 2 == 0
            even_j = j % 2 == 0
            even_k = k % 2 == 0
            if (
                self.cell_type[grid_idx_i, grid_idx_j, grid_idx_k]
                == CellType.SAND.value
            ):
                self.particle_active[i, j, k] = 1
                particle_pos_x, particle_pos_y, particle_pos_z = 0.0, 0.0, 0.0
                if even_i:
                    particle_pos_x = (
                        float(grid_idx_i) + 0.25 + ((ti.random(int) % 50) - 25) / 100.0
                    )
                else:
                    particle_pos_x = (
                        grid_idx_i + 0.75 + ((ti.random(int) % 50) - 25) / 100.0
                    )
                if even_j:
                    particle_pos_y = (
                        float(grid_idx_j) + 0.25 + ((ti.random(int) % 50) - 25) / 100.0
                    )
                else:
                    particle_pos_y = (
                        grid_idx_j + 0.75 + ((ti.random(int) % 50) - 25) / 100.0
                    )
                if even_k:
                    particle_pos_z = (
                        float(grid_idx_k) + 0.25 + ((ti.random(int) % 50) - 25) / 100.0
                    )
                else:
                    particle_pos_z = (
                        grid_idx_k + 0.75 + ((ti.random(int) % 50) - 25) / 100.0
                    )

                self.particle_pos[i, j, k] = [
                    particle_pos_x,
                    particle_pos_y,
                    particle_pos_z,
                ]
            else:
                self.particle_active[i, j, k] = 0

    # Taken from 4_fluid.py 252-0546-00L ETH Physically-Based Simulation in Computer Graphics course exercise
    @ti.func
    def clear_field(self, f: ti.template(), v: ti.template() = 0):
        for x, y, z in ti.ndrange(*f.shape):
            f[x, y, z] = v

    @ti.func
    def clamp(self, x, min_bound, max_bound):
        return max(min_bound, min(x, max_bound))

    @ti.kernel
    def update_cell_types(self):
        # Wipe out old cell_type grid and initialize the new grid with domain bounds.
        self.reset_cell_type()

        # Mark cells that contain at least one particle with SAND
        for i, j, k in self.particle_pos:
            if self.particle_active[i, j, k] == 1:
                p_pos = self.particle_pos[i, j, k]
                # Get cell idx in which the particle currently resides
                grid_i = self.clamp(int(p_pos[0]), 0, self.grid_size - 1)
                grid_j = self.clamp(int(p_pos[1]), 0, self.grid_size - 1)
                grid_k = self.clamp(int(p_pos[2]), 0, self.grid_size - 1)

                # Check whether cell is solid (boundary)
                if self.cell_type[grid_i, grid_j, grid_k] != CellType.SOLID.value:
                    self.cell_type[grid_i, grid_j, grid_k] = CellType.SAND.value

        # Mark the uninitialized cells with AIR
        for i, j, k in self.cell_type:
            if (
                self.cell_type[i, j, k] != CellType.SOLID.value
                and self.cell_type[i, j, k] != CellType.SAND.value
            ):
                self.cell_type[i, j, k] = CellType.AIR.value

    @ti.kernel
    def neumann_boundary_conditions(self):
        for i, j, k in self.cell_type:
            if i == 0 or i == self.grid_size - 1:
                self.v_x[i, j, k] = 0.0
                self.v_x[i + 1, j, k] = 0.0
            if j == 0 or j == self.grid_size - 1:
                self.v_y[i, j, k] = 0.0
                self.v_y[i, j + 1, k] = 0.0
            if k == 0 or k == self.grid_size - 1:
                self.v_z[i, j, k] = 0.0
                self.v_z[i, j, k + 1] = 0.0

    # Sample grid with grid origin at (x_offset, y_offset, z_offset)
    @ti.func
    def sample(
        self,
        grid,
        x,
        y,
        z,
        x_offset,
        y_offset,
        z_offset,
        x_resolution,
        y_resolution,
        z_resolution,
    ):
        x_down = self.clamp(int(x - x_offset), 0, x_resolution - 1)
        y_down = self.clamp(int(y - y_offset), 0, y_resolution - 1)
        z_down = self.clamp(int(z - z_offset), 0, z_resolution - 1)

        x_up = self.clamp(x_down + 1, 0, x_resolution - 1)
        y_up = self.clamp(y_down + 1, 0, y_resolution - 1)
        z_up = self.clamp(z_down + 1, 0, z_resolution - 1)

        diff_x = self.clamp(x - x_offset - x_down, 0.0, 1.0)
        diff_y = self.clamp(y - y_offset - y_down, 0.0, 1.0)
        diff_z = self.clamp(z - z_offset - z_down, 0.0, 1.0)

        x_val_front_down = (
            grid[x_down, y_down, z_down] * (1 - diff_x)
            + grid[x_up, y_down, z_down] * diff_x
        )
        x_val_back_down = (
            grid[x_down, y_down, z_up] * (1 - diff_x)
            + grid[x_up, y_down, z_up] * diff_x
        )
        x_val_front_up = (
            grid[x_down, y_up, z_down] * (1 - diff_x)
            + grid[x_up, y_up, z_down] * diff_x
        )
        x_val_back_up = (
            grid[x_down, y_up, z_up] * (1 - diff_x) + grid[x_up, y_up, z_up] * diff_x
        )

        xz_val_down = x_val_front_down * (1 - diff_z) + x_val_back_down * diff_z
        xz_val_up = x_val_front_up * (1 - diff_z) + x_val_back_up * diff_z

        return xz_val_down * (1 - diff_y) + xz_val_up * diff_y

    @ti.func
    def sample_cell_centered(self, grid, x, y, z):
        return self.sample(
            grid, x, y, z, 0.5, 0.5, 0.5, self.grid_size, self.grid_size, self.grid_size
        )

    @ti.func
    def sample_x_edged(self, grid, x, y, z):
        return self.sample(
            grid,
            x,
            y,
            z,
            0,
            0.5,
            0.5,
            self.grid_size + 1,
            self.grid_size,
            self.grid_size,
        )

    @ti.func
    def sample_y_edged(self, grid, x, y, z):
        return self.sample(
            grid,
            x,
            y,
            z,
            0.5,
            0.0,
            0.5,
            self.grid_size,
            self.grid_size + 1,
            self.grid_size,
        )

    @ti.func
    def sample_z_edged(self, grid, x, y, z):
        return self.sample(
            grid,
            x,
            y,
            z,
            0.5,
            0.5,
            0.0,
            self.grid_size,
            self.grid_size,
            self.grid_size + 1,
        )

    @ti.kernel
    def grid_to_particles(self):
        # For FLIP: Subtract the new grid velocities from the saved velocities
        for i, j, k in self.v_x:
            self.v_x_saved[i, j, k] = self.v_x_saved[i, j, k] - self.v_x[i, j, k]

        for i, j, k in self.v_y:
            self.v_y_saved[i, j, k] = self.v_y_saved[i, j, k] - self.v_y[i, j, k]

        for i, j, k in self.v_z:
            self.v_z_saved[i, j, k] = self.v_z_saved[i, j, k] - self.v_z[i, j, k]

        for i, j, k in self.particle_pos:
            if self.particle_active[i, j, k] == 1:
                pic_velocity = self.velocity_interpolation(
                    self.particle_pos[i, j, k], self.v_x, self.v_y, self.v_z
                )
                flip_velocity = self.velocity_interpolation(
                    self.particle_pos[i, j, k],
                    self.v_x_saved,
                    self.v_y_saved,
                    self.v_z_saved,
                )
                self.particle_v[i, j, k] = self.pic_fraction * pic_velocity + (
                    1.0 - self.pic_fraction
                ) * (self.particle_v[i, j, k] - flip_velocity)

    @ti.func
    def splat(
        self,
        target_field,
        particle_x,
        particle_y,
        particle_z,
        particle_value,
        weights,
        x_offset,
        y_offset,
        z_offset,
        x_resolution,
        y_resolution,
        z_resolution,
    ):
        x_down = self.clamp(int(particle_x - x_offset), 0, x_resolution - 1)
        y_down = self.clamp(int(particle_y - y_offset), 0, y_resolution - 1)
        z_down = self.clamp(int(particle_z - z_offset), 0, z_resolution - 1)

        x_up = self.clamp(x_down + 1, 0, x_resolution - 1)
        y_up = self.clamp(y_down + 1, 0, y_resolution - 1)
        z_up = self.clamp(z_down + 1, 0, z_resolution - 1)

        diff_x = self.clamp(particle_x - x_offset - x_down, 0.0, 1.0)
        diff_y = self.clamp(particle_y - y_offset - y_down, 0.0, 1.0)
        diff_z = self.clamp(particle_z - z_offset - z_down, 0.0, 1.0)

        target_field[x_down, y_down, z_down] += (
            particle_value * (1 - diff_x) * (1 - diff_y) * (1 - diff_z)
        )
        weights[x_down, y_down, z_down] += (1 - diff_x) * (1 - diff_y) * (1 - diff_z)

        target_field[x_up, y_down, z_down] += (
            particle_value * diff_x * (1 - diff_y) * (1 - diff_z)
        )
        weights[x_up, y_down, z_down] += diff_x * (1 - diff_y) * (1 - diff_z)

        target_field[x_down, y_up, z_down] += (
            particle_value * (1 - diff_x) * diff_y * (1 - diff_z)
        )
        weights[x_down, y_up, z_down] += (1 - diff_x) * diff_y * (1 - diff_z)

        target_field[x_down, y_down, z_up] += (
            particle_value * (1 - diff_x) * (1 - diff_y) * diff_z
        )
        weights[x_down, y_down, z_up] += (1 - diff_x) * (1 - diff_y) * diff_z

        target_field[x_up, y_down, z_up] += (
            particle_value * diff_x * (1 - diff_y) * diff_z
        )
        weights[x_up, y_down, z_up] += diff_x * (1 - diff_y) * diff_z

        target_field[x_up, y_up, z_down] += (
            particle_value * diff_x * diff_y * (1 - diff_z)
        )
        weights[x_up, y_up, z_down] += diff_x * diff_y * (1 - diff_z)

        target_field[x_down, y_up, z_up] += (
            particle_value * (1 - diff_x) * diff_y * diff_z
        )
        weights[x_down, y_up, z_up] += (1 - diff_x) * diff_y * diff_z

        target_field[x_up, y_up, z_up] += particle_value * diff_x * diff_y * diff_z
        weights[x_up, y_up, z_up] += diff_x * diff_y * diff_z

    @ti.func
    def splat_cell_centered(self, grid, x, y, z, value, weights):
        return self.splat(
            grid,
            x,
            y,
            z,
            value,
            weights,
            0.5,
            0.5,
            0.5,
            self.grid_size,
            self.grid_size,
            self.grid_size,
        )

    @ti.func
    def splat_x_edged(self, grid, x, y, z, value):
        return self.splat(
            grid,
            x,
            y,
            z,
            value,
            self.splat_x_weights,
            0,
            0.5,
            0.5,
            self.grid_size + 1,
            self.grid_size,
            self.grid_size,
        )

    @ti.func
    def splat_y_edged(self, grid, x, y, z, value):
        return self.splat(
            grid,
            x,
            y,
            z,
            value,
            self.splat_y_weights,
            0.5,
            0.0,
            0.5,
            self.grid_size,
            self.grid_size + 1,
            self.grid_size,
        )

    @ti.func
    def splat_z_edged(self, grid, x, y, z, value):
        return self.splat(
            grid,
            x,
            y,
            z,
            value,
            self.splat_z_weights,
            0.5,
            0.5,
            0.0,
            self.grid_size,
            self.grid_size,
            self.grid_size + 1,
        )

    # Splats the velocity of active particles to the grid.
    # Adds its weighted velocity to each surrounding grid vertex and in the end divides each grid vertex by the sum of all weights
    # that were applied to it.
    @ti.kernel
    def particles_to_grid(self):
        self.clear_field(self.v_x)
        self.clear_field(self.v_y)
        self.clear_field(self.v_z)
        self.clear_field(self.splat_x_weights)
        self.clear_field(self.splat_y_weights)
        self.clear_field(self.splat_z_weights)

        for i, j, k in self.particle_pos:
            if self.particle_active[i, j, k] == 1:
                p = self.particle_pos[i, j, k]
                v = self.particle_v[i, j, k]
                self.splat_x_edged(self.v_x, p[0], p[1], p[2], v[0])
                self.splat_y_edged(self.v_y, p[0], p[1], p[2], v[1])
                self.splat_z_edged(self.v_z, p[0], p[1], p[2], v[2])

        for i, j, k in self.splat_x_weights:
            if self.splat_x_weights[i, j, k] > 0.0:
                self.v_x[i, j, k] /= self.splat_x_weights[i, j, k]

        for i, j, k in self.splat_y_weights:
            if self.splat_y_weights[i, j, k] > 0.0:
                self.v_y[i, j, k] /= self.splat_y_weights[i, j, k]

        for i, j, k in self.splat_z_weights:
            if self.splat_z_weights[i, j, k] > 0.0:
                self.v_z[i, j, k] /= self.splat_z_weights[i, j, k]

    # Explicite euler step to advect particles
    @ti.kernel
    def advect_particles_explicit_euler(self, dt: ti.f32):
        for i, j, k in self.particle_pos:
            if self.particle_active[i, j, k] == 1:
                self.particle_pos[i, j, k] += dt * self.particle_v[i, j, k]

    @ti.kernel
    def update_particle_edge_pos(self):
        for i, j, k in self.particle_pos:
            grid_size = self.grid_size
            p_pos = self.particle_pos[i, j, k]
            # Get cell idx in which the particle currently resides
            grid_i = self.clamp(int(p_pos[0]), 0, grid_size - 1)
            grid_j = self.clamp(int(p_pos[1]), 0, grid_size - 1)
            grid_k = self.clamp(int(p_pos[2]), 0, grid_size - 1)

            # Check whether cell is solid (boundary)
            if (
                grid_i != 0
                and grid_i != grid_size - 1
                and grid_j != 0
                and grid_j != grid_size - 1
                and grid_k != 0
                and grid_k != grid_size - 1
                and self.cell_type[grid_i + 1, grid_j, grid_k] == CellType.SAND.value
                and self.cell_type[grid_i - 1, grid_j, grid_k] == CellType.SAND.value
                and self.cell_type[grid_i, grid_j + 1, grid_k] == CellType.SAND.value
                and self.cell_type[grid_i, grid_j - 1, grid_k] == CellType.SAND.value
                and self.cell_type[grid_i, grid_j, grid_k + 1] == CellType.SAND.value
                and self.cell_type[grid_i, grid_j, grid_k - 1] == CellType.SAND.value
            ):
                self.particle_edge_pos[i, j, k] = [0.0, 0.0, 0.0]

    # move particles with midpoint euler from grid velocity
    @ti.kernel
    def advect_particles_midpoint(self, dt: ti.f32):
        for i, j, k in self.particle_pos:
            if self.particle_active[i, j, k] == 1:
                start_pos = self.particle_pos[i, j, k]
                midpos = start_pos + self.velocity_interpolation(
                    start_pos, self.v_x, self.v_y, self.v_z
                ) * (dt * 0.5)
                step = (
                    self.velocity_interpolation(midpos, self.v_x, self.v_y, self.v_z)
                    * dt
                )
                self.particle_pos[i, j, k] += step

    @ti.func
    def velocity_interpolation(self, pos, vel_x, vel_y, vel_z):
        v_x = self.sample_x_edged(vel_x, pos.x, pos.y, pos.z)
        v_y = self.sample_y_edged(vel_y, pos.x, pos.y, pos.z)
        v_z = self.sample_z_edged(vel_z, pos.x, pos.y, pos.z)
        return ti.Vector([v_x, v_y, v_z])

    def save_velocities(self):
        self.v_x_saved.copy_from(self.v_x)
        self.v_y_saved.copy_from(self.v_y)
        self.v_z_saved.copy_from(self.v_z)

    def show_v_y(self):
        vely_numpy = self.v_y.to_numpy()
        resolution = min(vely_numpy.shape)
        fig = plt.figure()
        ax = fig.gca(projection="3d")
        ax.set_ylabel("z")
        ax.set_zlabel("y")
        ax.set_xlabel("x")

        x, y, z = np.meshgrid(
            np.arange(0, resolution, 1),
            np.arange(0, resolution, 1),
            np.arange(0, resolution, 1),
        )

        u = np.zeros((resolution, resolution, resolution))
        w = np.zeros((resolution, resolution, resolution))
        v = vely_numpy[:resolution, :resolution, :resolution]

        ax.quiver(y, z, x, u, w, v, length=1, color="black")
        plt.show()

    # Plots the pressure. Upwards pointing arrows mean positive pressure
    def show_pressure(self):
        p_numpy = self.pressure.to_numpy()
        resolution = min(p_numpy.shape)
        fig = plt.figure()
        ax = fig.gca(projection="3d")
        ax.set_ylabel("z")
        ax.set_zlabel("y")
        ax.set_xlabel("x")
        plt.xlim([0, resolution - 1])
        plt.ylim([0, resolution - 1])
        ax.set_zlim(0, resolution - 1)
        x, y, z = np.meshgrid(
            np.arange(0, resolution, 1),
            np.arange(0, resolution, 1),
            np.arange(0, resolution, 1),
        )

        u = np.zeros((resolution, resolution, resolution))
        w = np.zeros((resolution, resolution, resolution))
        v = p_numpy[:resolution, :resolution, :resolution]

        ax.quiver(y, z, x, u, w, v, length=1, color="black")
        plt.show()

    # Plots the rigid cells
    def show_rigid_cells(self):
        p_numpy = self.cell_rigid.to_numpy()
        resolution = min(p_numpy.shape)
        fig = plt.figure()
        ax = fig.gca(projection="3d")
        ax.set_ylabel("z")
        ax.set_zlabel("y")
        ax.set_xlabel("x")
        plt.xlim([0, resolution - 1])
        plt.ylim([0, resolution - 1])
        ax.set_zlim(0, resolution - 1)
        x, y, z = np.meshgrid(
            np.arange(0, resolution, 1),
            np.arange(0, resolution, 1),
            np.arange(0, resolution, 1),
        )

        u = np.zeros((resolution, resolution, resolution))
        w = np.zeros((resolution, resolution, resolution))
        v = p_numpy[:resolution, :resolution, :resolution]

        ax.quiver(y, z, x, u, w, v, length=1, color="black")
        plt.show()

    # Plots divergence. Upwards pointing values are positive.
    def show_divergence(self):
        div_numpy = self.divergence.to_numpy()
        resolution = min(div_numpy.shape)
        fig = plt.figure()
        ax = fig.gca(projection="3d")
        ax.set_ylabel("z")
        ax.set_zlabel("y")
        ax.set_xlabel("x")
        plt.xlim([0, resolution - 1])
        plt.ylim([0, resolution - 1])
        ax.set_zlim(0, resolution - 1)
        x, y, z = np.meshgrid(
            np.arange(0, resolution, 1),
            np.arange(0, resolution, 1),
            np.arange(0, resolution, 1),
        )

        u = np.zeros((resolution, resolution, resolution))
        w = np.zeros((resolution, resolution, resolution))
        v = div_numpy[:resolution, :resolution, :resolution]

        ax.quiver(y, z, x, u, w, v, length=1, color="black")
        plt.show()
