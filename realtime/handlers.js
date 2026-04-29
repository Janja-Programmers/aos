console.log("[AOS] realtime/handlers.js loaded");

function aosRealtimeHandlers(socket) {
	console.log(`[AOS] handlers attached to socket: ${socket.id}`);

	socket.onAny((event, data) => {
		console.log(`[AOS] socket event received: ${event}`, data);
	});

	socket.on("join_live_room", (data) => {
		console.log("[AOS] join_live_room handler reached", data);

		const liveId = data?.live_id;

		if (!liveId) {
			console.log("[AOS] join_live_room failed: live_id missing");

			socket.emit("aos_live_room_error", {
				message: "live_id is required",
			});
			return;
		}

		const room = `live:${liveId}`;

		socket.join(room);

		socket.emit("aos_live_room_joined", {
			room,
		});

		console.log(`[AOS] ${socket.id} joined ${room}`);
	});

	socket.on("leave_live_room", (data) => {
		console.log("[AOS] leave_live_room handler reached", data);

		const liveId = data?.live_id;

		if (!liveId) {
			console.log("[AOS] leave_live_room ignored: live_id missing");
			return;
		}

		const room = `live:${liveId}`;

		socket.leave(room);

		socket.emit("aos_live_room_left", {
			room,
		});

		console.log(`[AOS] ${socket.id} left ${room}`);
	});
}

module.exports = aosRealtimeHandlers;
